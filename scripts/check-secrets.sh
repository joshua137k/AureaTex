#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Impede commits com chaves, tokens ou passwords.
#
# Instalar uma vez (cria o hook pre-commit):
#     bash scripts/check-secrets.sh --install
#
# Correr à mão sobre o que está em stage:
#     bash scripts/check-secrets.sh
#
# Se for mesmo um falso positivo: git commit --no-verify
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"

if [[ "${1:-}" == "--install" ]]; then
  HOOK="$ROOT/.git/hooks/pre-commit"
  printf '#!/usr/bin/env bash\nexec bash "$(git rev-parse --show-toplevel)/scripts/check-secrets.sh"\n' > "$HOOK"
  chmod +x "$HOOK"
  echo "Hook instalado em .git/hooks/pre-commit"
  exit 0
fi

fail=0
red()  { printf '\033[31m%s\033[0m\n' "$*"; }

# 1. Ficheiros que nunca devem entrar.
BAD_NAMES='(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa.*|id_ed25519.*|.*\.sql(\.gz)?|.*\.dump|compose\.override\.yml|docker-compose\.override\.yml)$'
while IFS= read -r f; do
  [[ "$f" == ".env.example" ]] && continue
  if [[ "$f" =~ $BAD_NAMES ]]; then
    red "Ficheiro sensível em stage: $f"
    fail=1
  fi
done < <(git diff --cached --name-only --diff-filter=ACMR)

# 2. Padrões de chaves nas linhas acrescentadas.
PATTERNS=(
  '(ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}'          # tokens GitHub
  'github_pat_[A-Za-z0-9_]{40,}'
  'atx_[A-Za-z0-9_-]{30,}'                          # tokens MCP do AureaTeX
  'eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.' # JWT
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}'                                 # AWS
  'sk-[A-Za-z0-9_-]{32,}'                            # chaves de API (OpenAI, Anthropic...)
  'xox[baprs]-[0-9A-Za-z-]{10,}'                     # Slack
)
# Atribuições com valor literal, exceto exemplos e chamadas de funções:
#   ASSIGN_UP:  DB_PASSWORD: example123  /  GITHUB_TOKEN = "..."  (nomes em maiúsculas, yaml/env/código)
#   ASSIGN_LOW: password = "example123"  (string literal entre aspas)
ASSIGN_UP='\b[A-Z0-9_]*(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*["'"'"']?\s*[:=]\s*["'"'"']?[A-Za-z0-9+/=_@#%!.-]{8,}'
ASSIGN_LOW='\b(password|passwd|secret|api_key|apikey|client_secret)["'"'"']?\s*[:=]\s*["'"'"'][^"'"'"' ]{8,}["'"'"']'
ALLOW='change_this|change-this|your_|o_teu|example|placeholder|xxxx|<[^>]*>|\$\{|\$[A-Z_]|getenv|environ|[A-Za-z_.]+\(|aureatex\.(password|token)'

added="$(git diff --cached -U0 --diff-filter=ACMR -- . ':(exclude)app/static/pdfjs/*' ':(exclude)*.png' ':(exclude)*.vsix' ':(exclude)*package-lock.json' | grep -E '^\+[^+]' || true)"

for p in "${PATTERNS[@]}"; do
  hits="$(printf '%s\n' "$added" | grep -nE -- "$p" || true)"
  if [[ -n "$hits" ]]; then
    red "Possível chave/token (padrão: $p):"
    printf '%s\n' "$hits" | cut -c1-120 | sed 's/^/    /'
    fail=1
  fi
done

hits="$( { printf '%s\n' "$added" | grep -nE -- "$ASSIGN_UP"; printf '%s\n' "$added" | grep -inE -- "$ASSIGN_LOW"; } | grep -vE -- "$ALLOW" | sort -u || true)"
if [[ -n "$hits" ]]; then
  red "Possível password/segredo escrito no código:"
  printf '%s\n' "$hits" | cut -c1-120 | sed 's/^/    /'
  fail=1
fi

if [[ $fail -ne 0 ]]; then
  echo
  red "Commit bloqueado. Põe os segredos em variáveis de ambiente (.env / compose), não no código."
  echo "Se for um falso positivo: git commit --no-verify"
  exit 1
fi

echo "check-secrets: nada de sensível encontrado."
