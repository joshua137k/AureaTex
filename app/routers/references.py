"""
Referências académicas gratuitas:
  - CrossRef: pesquisa por título/DOI (sem autenticação)
  - OpenAlex: pesquisa avançada (sem autenticação)  
  - Zotero: biblioteca pessoal (API key gratuita)
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from core.database import get_db
from core.security import get_current_user

from routers.collab import sync_doc_content
from routers.collab import _rooms

import os
import re

router = APIRouter(prefix="/api/references", tags=["references"])

ZOTERO_API_URL = "https://api.zotero.org"


# ---------------------------------------------------------------------------
# Helpers BibTeX
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Remove caracteres problemáticos para BibTeX."""
    if not text:
        return ""
    return str(text).replace("{", "").replace("}", "").replace("\\", "").strip()


def _make_key(authors: list, year, title: str) -> str:
    """Gera chave BibTeX: PrimeiroAutorAnoTitulo."""
    first = ""
    if authors:
        last = authors[0].get("last", "") or authors[0].get("family", "")
        first = re.sub(r"[^a-zA-Z]", "", last)
    y = str(year or "")
    words = re.sub(r"[^a-zA-Z0-9 ]", "", title or "").split()[:2]
    return f"{first}{y}{''.join(words)}" or "ref"


def crossref_to_bibtex(item: dict) -> tuple[str, str]:
    """Converte item CrossRef para BibTeX. Devolve (key, bibtex)."""
    itype = item.get("type", "journal-article")
    type_map = {
        "journal-article":      "article",
        "proceedings-article":  "inproceedings",
        "book":                 "book",
        "book-chapter":         "incollection",
        "dissertation":         "phdthesis",
        "report":               "techreport",
        "posted-content":       "misc",
        "dataset":              "misc",
    }
    bib_type = type_map.get(itype, "misc")

    # Autores
    authors_raw = item.get("author", [])
    authors_bib = " and ".join(
        f"{_clean(a.get('family', ''))}, {_clean(a.get('given', ''))}"
        for a in authors_raw
    ).strip(", ")

    # Ano
    year = None
    for field in ("published-print", "published-online", "issued"):
        dp = item.get(field, {}).get("date-parts", [[]])[0]
        if dp:
            year = dp[0]
            break

    # Resolver problema das Strings vs Listas
    raw_title = item.get("title", "")
    title = _clean(raw_title[0] if isinstance(raw_title, list) and raw_title else raw_title)

    raw_source = item.get("container-title", "")
    source = _clean(raw_source[0] if isinstance(raw_source, list) and raw_source else raw_source)

    doi    = item.get("DOI", "")
    volume = item.get("volume", "")
    issue  = item.get("issue", "")
    pages  = item.get("page", "")
    
    raw_publisher = item.get("publisher", "")
    publisher = _clean(raw_publisher[0] if isinstance(raw_publisher, list) and raw_publisher else raw_publisher)
    
    issn   = (item.get("ISSN") or [""])[0] if isinstance(item.get("ISSN"), list) else item.get("ISSN", "")
    url    = item.get("URL", "")

    key = _make_key(authors_raw, year, title)

    fields = {}
    if authors_bib: fields["author"]    = f"{{{authors_bib}}}"
    if title:       fields["title"]     = f"{{{{{title}}}}}"
    if year:        fields["year"]      = str(year)
    if source:      fields["journal"]   = f"{{{source}}}" if bib_type == "article" else None
    if source and bib_type != "article":
                    fields["booktitle"] = f"{{{source}}}"
    if volume:      fields["volume"]    = volume
    if issue:       fields["number"]    = issue
    if pages:       fields["pages"]     = pages.replace("-", "--")
    if publisher:   fields["publisher"] = f"{{{publisher}}}"
    if doi:         fields["doi"]       = doi
    if url:         fields["url"]       = url
    if issn:        fields["issn"]      = issn

    # Limpar Nones
    fields = {k: v for k, v in fields.items() if v}
    fields_str = "\n".join(f"  {k:<12} = {v}," for k, v in fields.items())
    bib = f"@{bib_type}{{{key},\n{fields_str}\n}}"
    return key, bib


def openalex_to_bibtex(item: dict) -> tuple[str, str]:
    """Converte item OpenAlex para BibTeX."""
    itype = item.get("type", "article")
    bib_type = "article" if itype == "article" else "misc"
    
    authors_raw = item.get("authorships", [])
    authors_list = []
    for a in authors_raw:
        dn = a.get("author", {}).get("display_name", "")
        parts = dn.split()
        if len(parts) > 1:
            authors_list.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        elif parts:
            authors_list.append(parts[0])
            
    authors_bib = " and ".join(authors_list)
    title = _clean(item.get("title", ""))
    year = str(item.get("publication_year", ""))
    doi = (item.get("doi") or "").replace("https://doi.org/", "")
    
    source = ""
    pl = item.get("primary_location", {})
    if isinstance(pl, dict) and pl.get("source"):
        source = _clean(pl["source"].get("display_name", ""))
        
    first_author = [{"family": authors_list[0].split(",")[0]}] if authors_list else []
    key = _make_key(first_author, year, title)

    fields = {}
    if authors_bib: fields["author"] = f"{{{authors_bib}}}"
    if title:       fields["title"]  = f"{{{{{title}}}}}"
    if year:        fields["year"]   = year
    if source:      fields["journal"] = f"{{{source}}}"
    if doi:         fields["doi"]    = doi

    fields_str = "\n".join(f"  {k:<12} = {v}," for k, v in fields.items())
    bib = f"@{bib_type}{{{key},\n{fields_str}\n}}"
    return key, bib


def zotero_to_bibtex(item: dict) -> tuple[str, str]:
    """Converte item Zotero para BibTeX."""
    data = item.get("data", {})
    itype = data.get("itemType", "journalArticle")
    type_map = {
        "journalArticle":    "article",
        "conferencePaper":   "inproceedings",
        "book":              "book",
        "bookSection":       "incollection",
        "thesis":            "phdthesis",
        "report":            "techreport",
        "webpage":           "misc",
        "preprint":          "misc",
    }
    bib_type = type_map.get(itype, "misc")

    creators = data.get("creators", [])
    authors_raw = [
        {"family": c.get("lastName", ""), "given": c.get("firstName", "")}
        for c in creators if c.get("creatorType") == "author"
    ]
    authors_bib = " and ".join(
        f"{_clean(a['family'])}, {_clean(a['given'])}"
        for a in authors_raw
    )

    title     = _clean(data.get("title", ""))
    year      = (data.get("date", "") or "")[:4]
    source    = _clean(data.get("publicationTitle", "") or data.get("bookTitle", ""))
    doi       = data.get("DOI", "")
    volume    = data.get("volume", "")
    issue     = data.get("issue", "")
    pages     = data.get("pages", "")
    publisher = _clean(data.get("publisher", ""))
    url       = data.get("url", "")
    abstract  = _clean(data.get("abstractNote", ""))[:300]

    key = _make_key(authors_raw, year, title)

    fields = {}
    if authors_bib: fields["author"]    = f"{{{authors_bib}}}"
    if title:       fields["title"]     = f"{{{{{title}}}}}"
    if year:        fields["year"]      = year
    if source and bib_type == "article":
                    fields["journal"]   = f"{{{source}}}"
    elif source:    fields["booktitle"] = f"{{{source}}}"
    if volume:      fields["volume"]    = volume
    if issue:       fields["number"]    = issue
    if pages:       fields["pages"]     = pages.replace("-", "--")
    if publisher:   fields["publisher"] = f"{{{publisher}}}"
    if doi:         fields["doi"]       = doi
    if url:         fields["url"]       = url
    if abstract:    fields["abstract"]  = f"{{{abstract}}}"

    fields_str = "\n".join(f"  {k:<12} = {v}," for k, v in fields.items())
    bib = f"@{bib_type}{{{key},\n{fields_str}\n}}"
    return key, bib


# ---------------------------------------------------------------------------
# CrossRef / OpenAlex — pesquisa
# ---------------------------------------------------------------------------

@router.get("/search")
async def search_references(
    q: str = Query(..., min_length=3),
    source: str = Query(default="crossref"),
    limit: int  = Query(default=20, le=50),
):
    async with httpx.AsyncClient(timeout=15) as client:
        if source == "openalex":
            resp = await client.get(
                "https://api.openalex.org/works",
                params={
                    "search":      q,
                    "per-page":    limit,
                    "mailto":      "joshuatex@joshuadourado.pt",
                    "select":      "id,title,authorships,publication_year,primary_location,doi,type",
                },
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Erro ao contactar OpenAlex.")

            results = []
            for w in resp.json().get("results", []):
                authors = [
                    {
                        "family": (a["author"].get("display_name") or "").split()[-1],
                        "given":  " ".join((a["author"].get("display_name") or "").split()[:-1]),
                    }
                    for a in w.get("authorships", [])[:5]
                ]
                source_name = (w.get("primary_location") or {}).get("source") or {}
                results.append({
                    "title":      w.get("title", ""),
                    "authors":    [f"{a['family']}, {a['given']}" for a in authors],
                    "year":       w.get("publication_year"),
                    "source":     source_name.get("display_name", "") if isinstance(source_name, dict) else "",
                    "doi":        (w.get("doi") or "").replace("https://doi.org/", ""),
                    "type":       w.get("type", ""),
                    "_raw_source": "openalex",
                    "_raw":       w,
                })
            return results
        else:
            resp = await client.get(
                "https://api.crossref.org/works",
                params={
                    "query":            q,
                    "rows":             limit,
                    "mailto":           "joshuatex@joshuadourado.pt",
                    "select":           "DOI,title,author,published-print,published-online,issued,type,container-title,volume,issue,page,publisher,URL,ISSN",
                },
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Erro ao contactar CrossRef.")

            results = []
            for item in resp.json().get("message", {}).get("items", []):
                year = None
                for f in ("published-print", "published-online", "issued"):
                    dp = item.get(f, {}).get("date-parts", [[]])[0]
                    if dp:
                        year = dp[0]
                        break

                results.append({
                    "title":   (item.get("title")  or [""])[0] if isinstance(item.get("title"), list) else item.get("title", ""),
                    "authors": [
                        f"{a.get('family','')}, {a.get('given','')}"
                        for a in item.get("author", [])[:5]
                    ],
                    "year":    year,
                    "source":  (item.get("container-title") or [""])[0] if isinstance(item.get("container-title"), list) else item.get("container-title", ""),
                    "doi":     item.get("DOI", ""),
                    "type":    item.get("type", ""),
                    "_raw_source": "crossref",
                    "_raw":    item,
                })
            return results


@router.get("/doi/{doi:path}")
async def get_by_doi(doi: str):
    """Resolve um DOI e devolve os metadados + BibTeX."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.crossref.org/works/{doi}",
            params={"mailto": "joshuatex@joshuadourado.pt"},
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="DOI não encontrado.")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Erro ao resolver DOI.")

    item = resp.json().get("message", {})
    key, bib = crossref_to_bibtex(item)
    year = None
    for f in ("published-print", "published-online", "issued"):
        dp = item.get(f, {}).get("date-parts", [[]])[0]
        if dp:
            year = dp[0]
            break

    return {
        "key":     key,
        "bibtex":  bib,
        "title":   (item.get("title") or [""])[0] if isinstance(item.get("title"), list) else item.get("title", ""),
        "authors": [f"{a.get('family','')}, {a.get('given','')}" for a in item.get("author", [])],
        "year":    year,
        "doi":     doi,
        "_raw":    item,
        "_raw_source": "crossref"
    }


# ---------------------------------------------------------------------------
# Zotero
# ---------------------------------------------------------------------------

@router.get("/zotero")
async def get_zotero_refs(user=Depends(get_current_user), cur=Depends(get_db), limit: int = Query(default=100, le=500)):
    await cur.execute("SELECT zotero_user_id, zotero_api_key FROM users WHERE id = %s", (user["id"],))
    row = await cur.fetchone()
    if not row or not row.get("zotero_api_key"):
        raise HTTPException(status_code=401, detail="Zotero não configurado.")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{ZOTERO_API_URL}/users/{row['zotero_user_id']}/items",
            headers={"Zotero-API-Key": row["zotero_api_key"]},
            params={"limit": limit, "format": "json", "itemType": "-attachment"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Erro ao contactar Zotero.")

    items = resp.json()
    return [
        {
            "key":     item["key"],
            "title":   item["data"].get("title", ""),
            "authors": [
                f"{c.get('lastName','')}, {c.get('firstName','')}"
                for c in item["data"].get("creators", [])
                if c.get("creatorType") == "author"
            ],
            "year":    (item["data"].get("date", "") or "")[:4],
            "source":  item["data"].get("publicationTitle", ""),
            "type":    item["data"].get("itemType", ""),
            "_raw":    item,
        }
        for item in items
    ]


@router.post("/zotero/connect")
async def connect_zotero(body: dict, user=Depends(get_current_user), cur=Depends(get_db)):
    zotero_user_id = body.get("user_id", "").strip()
    api_key        = body.get("api_key", "").strip()
    if not zotero_user_id or not api_key:
        raise HTTPException(status_code=400, detail="user_id e api_key são obrigatórios.")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{ZOTERO_API_URL}/users/{zotero_user_id}/items?limit=1",
            headers={"Zotero-API-Key": api_key},
        )
        if resp.status_code == 403:
            raise HTTPException(status_code=400, detail="API key inválida ou sem permissões.")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Erro ao verificar credenciais Zotero.")

    await cur.execute(
        "UPDATE users SET zotero_user_id = %s, zotero_api_key = %s WHERE id = %s",
        (zotero_user_id, api_key, user["id"]),
    )
    return {"status": "connected"}


@router.get("/zotero/status")
async def zotero_status(user=Depends(get_current_user), cur=Depends(get_db)):
    await cur.execute("SELECT zotero_api_key FROM users WHERE id = %s", (user["id"],))
    row = await cur.fetchone()
    return {"connected": bool(row and row.get("zotero_api_key"))}


# ---------------------------------------------------------------------------
# Exportar BibTeX
# ---------------------------------------------------------------------------

@router.post("/export-bib/{project_id}")
async def export_bib(project_id: int, body: dict, user=Depends(get_current_user), cur=Depends(get_db)):
    await cur.execute(
        """
        SELECT id FROM projects WHERE id = %s AND (
            owner_id = %s OR EXISTS (
                SELECT 1 FROM project_collaborators pc
                WHERE pc.project_id = %s AND pc.user_id = %s
            )
        )
        """,
        (project_id, user["id"], project_id, user["id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    items = body.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="Nenhum item para exportar.")

    existing_bib = ""
    if project_id in _rooms:
        room = _rooms[project_id]
        if 'refs.bib' in room.docs:
            existing_bib = room.docs['refs.bib'].content

    if not existing_bib:
        await cur.execute(
            "SELECT content FROM project_files WHERE project_id = %s AND filename = 'refs.bib'",
            (project_id,),
        )
        existing = await cur.fetchone()
        existing_bib = existing["content"] if existing else ""

    existing_keys = set(re.findall(r"@\w+\{(\w+),", existing_bib))

    new_entries = []
    skipped     = 0

    for item in items:
        raw    = item.get("_raw", {})
        source = item.get("_raw_source", "crossref")

        if source == "zotero":
            key, bib = zotero_to_bibtex({"data": raw} if "itemType" in raw else raw)
        elif source == "openalex":
            key, bib = openalex_to_bibtex(raw)
        else:
            key, bib = crossref_to_bibtex(raw)

        if key in existing_keys:
            skipped += 1
            continue

        existing_keys.add(key)
        new_entries.append(bib)

    if not new_entries and skipped > 0:
        return {
            "status":  "skipped",
            "message": f"Todas as {skipped} referências já existem no refs.bib.",
            "count":   0,
        }

    header = "% Gerado pelo JoshuaTeX — não editar manualmente as chaves\n\n"
    if existing_bib:
        full_bib = existing_bib.rstrip() + "\n\n" + "\n\n".join(new_entries)
    else:
        full_bib = header + "\n\n".join(new_entries)

    await cur.execute(
        """
        INSERT INTO project_files (project_id, filename, content)
        VALUES (%s, 'refs.bib', %s)
        ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
        """,
        (project_id, full_bib),
    )


    await sync_doc_content(project_id, 'refs.bib', full_bib)

    msg = f"{len(new_entries)} referência(s) adicionadas ao refs.bib"
    if skipped:
        msg += f" ({skipped} já existiam, ignoradas)"

    return {"status": "success", "message": msg, "count": len(new_entries)}