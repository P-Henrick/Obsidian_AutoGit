import argparse
import json
import tempfile
import os
import shlex
import subprocess
import sys
import time
from typing import List, Optional


def _runtime_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

# Evita janelas de CMD piscando no Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


GIT_TIMEOUT_SECONDS = 180
MAX_COMMIT_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB por commit

# Arquivo que persiste repositórios adicionados manualmente via /addrepo
EXTRA_REPOS_FILE = os.path.join(_runtime_dir(), "repos_extra.json")


def run_git(
    repo_path: str,
    args: List[str],
    check: bool = True,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "Never")

    return subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        env=env,
        timeout=timeout_seconds,
        creationflags=_NO_WINDOW,
    )


# ── Gerenciamento de repos extras (adicionados manualmente) ───────────────────

def load_extra_repos() -> List[str]:
    """Carrega a lista de repositórios adicionados manualmente."""
    if not os.path.exists(EXTRA_REPOS_FILE):
        return []
    try:
        with open(EXTRA_REPOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, str)]
    except Exception:
        return []


def save_extra_repos(repos: List[str]) -> None:
    """Salva a lista de repositórios extras no arquivo JSON."""
    with open(EXTRA_REPOS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(set(repos)), f, indent=2, ensure_ascii=False)


def add_extra_repo(path: str) -> str:
    """Adiciona um repositório à lista manual. Retorna mensagem de status."""
    repo_path = normalize_repo_path(path)
    validate_repo(repo_path)  # lança exceção se inválido
    repos = load_extra_repos()
    if repo_path in repos:
        return f"Repositório já está na lista: {repo_path}"
    repos.append(repo_path)
    save_extra_repos(repos)
    return f"Repositório adicionado: {repo_path}"


def remove_extra_repo(path: str) -> str:
    """Remove um repositório da lista manual. Retorna mensagem de status."""
    repo_path = normalize_repo_path(path)
    repos = load_extra_repos()
    if repo_path not in repos:
        return f"Repositório não encontrado na lista: {repo_path}"
    repos.remove(repo_path)
    save_extra_repos(repos)
    return f"Repositório removido: {repo_path}"


# ─────────────────────────────────────────────────────────────────────────────

def normalize_repo_path(path: str) -> str:
    cleaned = path.strip()
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (
        cleaned.startswith("'") and cleaned.endswith("'")
    ):
        cleaned = cleaned[1:-1].strip()

    repo_path = os.path.abspath(cleaned)
    if repo_path.lower().endswith(".git"):
        repo_path = os.path.dirname(repo_path)
    return repo_path


def validate_repo(repo_path: str) -> None:
    if not os.path.isdir(repo_path):
        raise FileNotFoundError(f"Caminho não encontrado: {repo_path}")

    result = run_git(repo_path, ["rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError(f"Caminho não é um repositório Git válido: {repo_path}")


def _run_git_binary(repo_path: str, args: List[str]) -> bytes:
    """Executa git e retorna stdout como bytes puro (sem decodificação)."""
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "Never")
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        check=True,
        env=env,
        timeout=GIT_TIMEOUT_SECONDS,
        creationflags=_NO_WINDOW,
    )
    return result.stdout


def _parse_nul_list(raw: bytes) -> List[str]:
    """Converte uma saída git delimitada por \\0 em lista de paths (sem itens vazios)."""
    return [
        entry.decode("utf-8", errors="replace")
        for entry in raw.split(b"\0")
        if entry
    ]


def get_changed_files(repo_path: str) -> List[str]:
    """
    Retorna todos os arquivos com alterações pendentes (modificados, deletados, novos).

    Estratégia combinada para garantir cobertura total:
      1. git status --porcelain=v1 -z  → captura alterações em arquivos rastreados
         (modificados, deletados, renomeados, copiados, conflitos).
         Para renomeações (R/C), inclui AMBOS os caminhos (novo e antigo).
      2. git ls-files --others --exclude-standard -z  → captura INDIVIDUALMENTE cada
         arquivo novo não rastreado, incluindo arquivos dentro de subdiretórios novos
         (git status por padrão só mostra o diretório raiz, não arquivos individuais).
    """
    seen: set = set()
    files: List[str] = []

    def add(path: str) -> None:
        if path and path not in seen:
            seen.add(path)
            files.append(path)

    # ── 1. Arquivos rastreados com alterações (status não-??) ──────────────────
    status_raw = _run_git_binary(repo_path, ["status", "--porcelain=v1", "-z"])
    raw_entries = status_raw.split(b"\0")

    i = 0
    while i < len(raw_entries):
        raw = raw_entries[i]
        if not raw or len(raw) < 3:
            i += 1
            continue

        status = raw[:2].decode("utf-8", errors="replace")
        xy = status.strip()

        # Ignora entradas puramente não rastreadas — tratadas no passo 2
        if xy == "??":
            i += 1
            continue

        new_path = raw[3:].decode("utf-8", errors="replace").strip('"')

        if status[0] in {"R", "C"}:
            # Formato: XY NOVO_PATH\0ANTIGO_PATH\0
            # Inclui o caminho NOVO (o arquivo que passou a existir)
            add(new_path)
            # Inclui o caminho ANTIGO (para que a remoção do nome antigo seja staged)
            if i + 1 < len(raw_entries) and raw_entries[i + 1]:
                old_path = raw_entries[i + 1].decode("utf-8", errors="replace").strip('"')
                add(old_path)
                i += 1  # pula a entrada do caminho antigo
        else:
            add(new_path)

        i += 1

    # ── 2. Arquivos novos não rastreados (individualmente) ─────────────────────
    # ls-files --others expande diretórios novos em arquivos individuais,
    # garantindo que nenhum arquivo dentro de pastas novas seja ignorado.
    try:
        untracked_raw = _run_git_binary(
            repo_path,
            ["ls-files", "--others", "--exclude-standard", "-z"],
        )
        for path in _parse_nul_list(untracked_raw):
            add(path)
    except subprocess.CalledProcessError:
        # Fallback: usa entradas ?? do status já processado acima caso ls-files falhe
        i = 0
        while i < len(raw_entries):
            raw = raw_entries[i]
            if not raw or len(raw) < 3:
                i += 1
                continue
            status = raw[:2].decode("utf-8", errors="replace").strip()
            if status == "??":
                path = raw[3:].decode("utf-8", errors="replace").strip('"')
                add(path)
            i += 1

    return files


def stage_chunk(repo_path: str, chunk: List[str]) -> None:
    run_git(repo_path, ["reset", "-q"])

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp_path = temp.name
            payload = "\0".join(chunk) + "\0"
            temp.write(payload.encode("utf-8"))

        run_git(
            repo_path,
            [
                "add",
                "-A",
                f"--pathspec-from-file={temp_path}",
                "--pathspec-file-nul",
            ],
        )
    except subprocess.CalledProcessError:
        for file_path in chunk:
            run_git(repo_path, ["add", "-A", "--", file_path], check=False)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def get_file_size(repo_path: str, file_path: str) -> int:
    """Retorna o tamanho do arquivo em bytes; 0 se não existir (ex.: arquivo deletado)."""
    full_path = os.path.join(repo_path, file_path)
    try:
        return os.path.getsize(full_path)
    except OSError:
        return 0


def split_chunk_by_size(
    repo_path: str, chunk: List[str], max_bytes: int
) -> List[List[str]]:
    """Subdivide um chunk de arquivos em sub-chunks onde cada um respeita max_bytes."""
    sub_chunks: List[List[str]] = []
    current: List[str] = []
    current_size = 0

    for f in chunk:
        size = get_file_size(repo_path, f)
        if size >= max_bytes:
            raise ValueError(
                f"Arquivo '{f}' possui {size / (1024 ** 3):.2f} GB e excede o limite de "
                f"{max_bytes / (1024 ** 3):.0f} GB por commit/push. "
                "Remova ou mova o arquivo antes de continuar."
            )
        elif current_size + size > max_bytes and current:
            sub_chunks.append(current)
            current = [f]
            current_size = size
        else:
            current.append(f)
            current_size += size

    if current:
        sub_chunks.append(current)

    return sub_chunks if sub_chunks else [[]]


def get_current_branch(repo_path: str) -> str:
    result = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = result.stdout.strip()
    if not branch:
        raise ValueError("Não foi possível identificar a branch atual.")
    return branch


def push_with_upstream_fallback(repo_path: str) -> None:
    try:
        run_git(repo_path, ["push"])
        return
    except subprocess.CalledProcessError as exc:
        details = f"{(exc.stderr or '').strip()}\n{(exc.stdout or '').strip()}".lower()
        if "no upstream" not in details and "set-upstream" not in details:
            raise

    branch = get_current_branch(repo_path)
    run_git(repo_path, ["push", "-u", "origin", branch])


def commit_in_batches(
    repo_path: str,
    summary: str,
    description: Optional[str],
    batch_size: int,
    delay_seconds: float,
    stop_event=None,
) -> bool:
    def stop_requested() -> bool:
        return bool(stop_event and hasattr(stop_event, "is_set") and stop_event.is_set())

    if stop_requested():
        print("Operação interrompida antes de iniciar.")
        return False

    print("Verificando arquivos alterados...", flush=True)
    files = get_changed_files(repo_path)
    total = len(files)

    if total == 0:
        print("Nenhum arquivo alterado para commit.")
        return True

    print(f"Total de arquivos pendentes: {total}")

    # Pré-calcula tamanho total para informar o usuário
    total_bytes = sum(get_file_size(repo_path, f) for f in files)
    print(
        f"Tamanho estimado total: {total_bytes / (1024 ** 2):.1f} MB "
        f"(limite por commit: {MAX_COMMIT_BYTES // (1024 ** 3)} GB)"
    )

    # Divide em chunks por contagem e depois por tamanho
    count_chunks = [files[s:s + batch_size] for s in range(0, total, batch_size)]
    size_chunks: List[List[str]] = []
    for cc in count_chunks:
        size_chunks.extend(split_chunk_by_size(repo_path, cc, MAX_COMMIT_BYTES))

    total_commits = len(size_chunks)
    if total_commits > len(count_chunks):
        print(
            f"Atenção: chunks subdivididos por tamanho. "
            f"Total de commits necessários: {total_commits}"
        )

    commit_index = 0
    files_done = 0
    for chunk in size_chunks:
        if stop_requested():
            print("Operação interrompida pelo usuário.")
            return False

        if not chunk:
            continue
        commit_index += 1
        chunk_bytes = sum(get_file_size(repo_path, f) for f in chunk)

        print(
            f"Iniciando lote #{commit_index}/{total_commits} — "
            f"{len(chunk)} arquivo(s), {chunk_bytes / (1024 ** 2):.1f} MB...",
            flush=True,
        )

        # Validação de tamanho por commit
        print(f"Validando tamanho do commit ({chunk_bytes / (1024 ** 2):.1f} MB)...", flush=True)
        if chunk_bytes >= MAX_COMMIT_BYTES:
            raise ValueError(
                f"Lote #{commit_index} possui {chunk_bytes / (1024 ** 3):.2f} GB e excede o limite de "
                f"{MAX_COMMIT_BYTES // (1024 ** 3)} GB por commit. Operação abortada."
            )

        stage_chunk(repo_path, chunk)

        message = summary
        if description and description.strip():
            message = f"{summary}\n\n{description.strip()}"

        print("Criando commit...", flush=True)
        run_git(repo_path, ["commit", "-m", message])

        # Validação de tamanho por push (mesmo conjunto de arquivos do commit)
        print(f"Validando tamanho do push ({chunk_bytes / (1024 ** 2):.1f} MB)...", flush=True)
        if chunk_bytes >= MAX_COMMIT_BYTES:
            raise ValueError(
                f"Push do lote #{commit_index} possui {chunk_bytes / (1024 ** 3):.2f} GB e excede o limite de "
                f"{MAX_COMMIT_BYTES // (1024 ** 3)} GB por push. Operação abortada."
            )

        print("Enviando push...", flush=True)
        push_with_upstream_fallback(repo_path)

        files_done += len(chunk)
        print(
            f"Commit #{commit_index} concluído com {len(chunk)} arquivo(s). "
            f"Progresso: {files_done}/{total}"
        )
        print("Push concluído para o commit atual.")

        remaining = total - files_done
        if remaining > 0 and delay_seconds > 0:
            print(f"Aguardando {delay_seconds:g}s para o próximo commit...")
            end_time = time.time() + delay_seconds
            while time.time() < end_time:
                if stop_requested():
                    print("Operação interrompida pelo usuário.")
                    return False
                time.sleep(min(0.25, max(0.0, end_time - time.time())))

    print("Todos os commits em lote foram concluídos.")
    return True


def find_git_repos(root_path: str) -> List[str]:
    """Encontra todos os repositórios Git sob root_path (não recursivo em repos aninhados)."""
    repos: List[str] = []
    root = os.path.abspath(root_path)

    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            repos.append(dirpath)
            # Não entra dentro de sub-repositórios
            dirnames.clear()

    return sorted(repos)


def _collect_repos(root_path: str) -> List[str]:
    """Mescla repos descobertos automaticamente + manuais (sem duplicatas)."""
    auto_repos = find_git_repos(root_path)
    extra_repos = load_extra_repos()
    seen: set = set()
    repos: List[str] = []
    for r in auto_repos + extra_repos:
        r_norm = os.path.abspath(r)
        if r_norm not in seen:
            seen.add(r_norm)
            repos.append(r_norm)
    repos.sort()
    return repos


# ── Pull automático (remoto → local) ─────────────────────────────────────────

def pull_repo(repo_path: str) -> str:
    """
    Busca atualizações do remoto e aplica localmente.
    Retorna uma string descrevendo o resultado: 'atualizado', 'sem novidades' ou 'erro: ...'.
    """
    # 1. Baixa metadados do remoto sem alterar arquivos
    run_git(repo_path, ["fetch", "--quiet"])

    # 2. Conta commits que existem no remoto mas ainda não no local
    result = run_git(
        repo_path,
        ["rev-list", "HEAD..@{u}", "--count"],
        check=False,
    )
    count_str = result.stdout.strip()

    # Se não houver upstream configurado, rev-list falha — tenta pull mesmo assim
    if result.returncode != 0 or not count_str.isdigit():
        pull_result = run_git(repo_path, ["pull", "--ff-only", "--quiet"], check=False)
        if pull_result.returncode == 0:
            return "atualizado (upstream não rastreado; pull aplicado)"
        return f"sem upstream rastreado ({(pull_result.stderr or '').strip()})"

    behind = int(count_str)
    if behind == 0:
        return "sem novidades"

    # 3. Aplica os commits do remoto com fast-forward
    run_git(repo_path, ["pull", "--ff-only", "--quiet"])
    return f"{behind} commit(s) puxado(s)"


def autopull_cycle(repos: List[str]) -> None:
    """Executa um ciclo de git pull em todos os repositórios listados."""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"Ciclo pull — {len(repos)} repositório(s)")
    print(sep)

    updated = 0
    skipped = 0
    errors = 0

    for repo in repos:
        repo_name = os.path.basename(repo)
        print(f"\n→ [{repo_name}]  {repo}", flush=True)
        try:
            validate_repo(repo)
            status = pull_repo(repo)
            print(f"  {status}")
            if "sem novidades" in status or "sem upstream" in status:
                skipped += 1
            else:
                updated += 1
        except subprocess.TimeoutExpired as exc:
            cmd = " ".join(exc.cmd) if isinstance(exc.cmd, list) else str(exc.cmd)
            print(f"  TIMEOUT: {cmd}")
            errors += 1
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or str(exc)
            # Fast-forward conflict: tem commits locais não enviados
            if "not possible to fast-forward" in detail.lower() or "diverged" in detail.lower():
                print(f"  AVISO: branch divergiu do remoto — pull manual necessário.")
            else:
                print(f"  ERRO git: {detail}")
            errors += 1
        except Exception as exc:
            print(f"  ERRO: {exc}")
            errors += 1

    print(f"\n{sep}")
    print(
        f"Ciclo concluído — Atualizados: {updated}  |  Sem novidades: {skipped}  |  Erros: {errors}"
    )
    print(sep)


def autopull_loop(
    root_path: str,
    interval_seconds: int = 60,
) -> None:
    """Loop infinito: verifica atualizações do remoto a cada interval_seconds."""
    root_path = os.path.abspath(root_path)
    if not os.path.isdir(root_path):
        print(f"Diretório não encontrado: {root_path}")
        return

    print("Auto-pull iniciado.")
    print(f"  Raiz      : {root_path}")
    print(f"  Intervalo : {interval_seconds}s")
    print("Pressione Ctrl+C para encerrar.\n")

    try:
        while True:
            repos = _collect_repos(root_path)
            extra_repos = load_extra_repos()
            if extra_repos:
                print(f"  + {len(extra_repos)} repositório(s) manual(is) incluso(s) via /addrepo")
            if not repos:
                print(f"Nenhum repositório encontrado em: {root_path}")
                print("  Dica: use /addrepo <caminho> para adicionar manualmente.")
            else:
                autopull_cycle(repos)

            next_run = time.time() + interval_seconds
            while time.time() < next_run:
                remaining = int(next_run - time.time())
                print(
                    f"\rPróximo ciclo em {remaining:3d}s... (Ctrl+C para sair)",
                    end="",
                    flush=True,
                )
                time.sleep(1)
            print()
    except KeyboardInterrupt:
        print("\nAuto-pull encerrado pelo usuário.")


# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="/commit",
        description=(
            "Faz commits automáticos em lote. "
            "Exemplo: /commit \"C:\\Repo\" \"Summary\" \"Description opcional\""
        ),
    )
    parser.add_argument("repo_path", help="Caminho do repositório (pasta raiz ou pasta .git)")
    parser.add_argument("summary", help="Resumo do commit (obrigatório)")
    parser.add_argument("description", nargs="?", default="", help="Descrição opcional")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Quantidade de arquivos por commit (padrão: 100)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5,
        help="Segundos entre commits (padrão: 5)",
    )
    return parser


def handle_commit_command(raw_command: str) -> None:
    parser = build_parser()

    try:
        args = shlex.split(raw_command, posix=False)
    except ValueError as exc:
        print(f"Erro ao ler comando: {exc}")
        return

    if not args:
        print("Comando vazio.")
        return

    if args[0].lower() != "/commit":
        print("Comando inválido. Use /commit")
        return

    namespace, unknown_args = parser.parse_known_args(args[1:])
    if unknown_args:
        print(f"Argumentos não reconhecidos: {' '.join(unknown_args)}")
        print("Use /help para ver o formato correto.")
        return

    if namespace.batch_size <= 0:
        print("--batch-size deve ser maior que 0.")
        return

    if namespace.delay < 0:
        print("--delay não pode ser negativo.")
        return

    repo_path = normalize_repo_path(namespace.repo_path)

    try:
        print(f"Validando repositório: {repo_path}", flush=True)
        validate_repo(repo_path)

        print("Iniciando fluxo de commit em lote...", flush=True)
        commit_in_batches(
            repo_path=repo_path,
            summary=namespace.summary,
            description=namespace.description,
            batch_size=namespace.batch_size,
            delay_seconds=namespace.delay,
        )
    except subprocess.TimeoutExpired as exc:
        cmd = " ".join(exc.cmd) if isinstance(exc.cmd, list) else str(exc.cmd)
        print(f"Tempo limite excedido ({GIT_TIMEOUT_SECONDS}s) ao executar: {cmd}")
        print("Dica: teste o comando manualmente no terminal para checar rede/credenciais/permissões.")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        details_lower = details.lower()

        if (
            "terminal prompts disabled" in details_lower
            or "could not read username" in details_lower
            or "authentication failed" in details_lower
        ):
            print(
                "Falha de autenticação no Git. Configure credenciais (ex.: Git Credential Manager/PAT) "
                "e tente novamente."
            )
        print(f"Falha ao executar git: {details}")
    except Exception as exc:
        print(f"Erro: {exc}")


def _handle_addrepo_command(raw_command: str) -> None:
    """Interpreta /addrepo <caminho> e registra o repositório."""
    parts = raw_command.strip().split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        print("Uso: /addrepo <caminho_do_repositório>")
        return
    path = parts[1].strip().strip('"').strip("'")
    try:
        msg = add_extra_repo(path)
        print(msg)
        all_repos = load_extra_repos()
        print(f"Total de repositórios na lista manual: {len(all_repos)}")
    except (FileNotFoundError, ValueError) as exc:
        print(f"Erro: {exc}")


def _handle_removerepo_command(raw_command: str) -> None:
    """Interpreta /removerepo <caminho> e remove o repositório da lista."""
    parts = raw_command.strip().split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        print("Uso: /removerepo <caminho_do_repositório>")
        return
    path = parts[1].strip().strip('"').strip("'")
    msg = remove_extra_repo(path)
    print(msg)


def _handle_listrepos_command() -> None:
    """Lista todos os repositórios registrados manualmente."""
    repos = load_extra_repos()
    if not repos:
        print("Nenhum repositório cadastrado manualmente.")
        print("Use /addrepo <caminho> para adicionar um.")
        return
    print(f"Repositórios manuais ({len(repos)}):")
    for i, r in enumerate(repos, 1):
        exists = "OK" if os.path.isdir(r) else "NÃO ENCONTRADO"
        print(f"  {i:2d}. [{exists}] {r}")


def _handle_autopull_command(raw_command: str) -> None:
    """Interpreta e executa o comando /autopull do REPL."""
    ap_parser = argparse.ArgumentParser(prog="/autopull", add_help=False)
    ap_parser.add_argument("root_path", help="Diretório raiz com os repositórios")
    ap_parser.add_argument("--interval", type=int, default=60, help="Segundos entre ciclos (padrão: 60)")

    try:
        parts = shlex.split(raw_command, posix=False)
    except ValueError as exc:
        print(f"Erro ao ler comando: {exc}")
        return

    try:
        ns = ap_parser.parse_args(parts[1:])
    except SystemExit:
        print("Uso: /autopull <root_path> [--interval 60]")
        return

    autopull_loop(
        root_path=normalize_repo_path(ns.root_path),
        interval_seconds=ns.interval,
    )


def repl() -> None:
    print("Auto Commit CLI")
    print("Comandos principais:")
    print("  /autopull <raiz> [--interval 60]  — puxa atualiz. do remoto a cada N segundos")
    print("  /commit <repo> <msg> [opções]     — commit+push manual em um repositório")
    print("  /addrepo <caminho>                 — adiciona repo à lista manual")
    print("  /removerepo <caminho>              — remove repo da lista manual")
    print("  /listrepos                         — lista repos manuais cadastrados")
    print("  /help  /exit")
    print("Dica: use aspas quando houver espaços no caminho ou na mensagem.")

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando.")
            break

        if not raw:
            continue

        lower = raw.lower()

        if lower in {"/exit", "exit", "quit", "/quit"}:
            print("Encerrado.")
            break

        if lower == "/help":
            print("Comandos disponíveis:")
            print('  /autopull "C:\\GitHub" [--interval 60]')
            print("    → Verifica atualizações do remoto e puxa (git pull) a cada N segundos.")
            print('  /commit "C:\\Repo" "Summary" ["Descrição"] [--batch-size 100] [--delay 5]')
            print("    → Commit+push manual em um repositório.")
            print('  /addrepo "C:\\MeuRepo"    — adiciona repositório à lista manual')
            print('  /removerepo "C:\\MeuRepo" — remove repositório da lista manual')
            print('  /listrepos               — lista todos os repositórios manuais')
            print('  /exit                    — encerra o programa')
            continue

        if raw.lower().startswith("/autopull"):
            _handle_autopull_command(raw)
            continue

        if raw.lower().startswith("/addrepo"):
            _handle_addrepo_command(raw)
            continue

        if raw.lower().startswith("/removerepo"):
            _handle_removerepo_command(raw)
            continue

        if lower == "/listrepos":
            _handle_listrepos_command()
            continue

        if raw.startswith("/commit"):
            handle_commit_command(raw)
            continue

        print("Comando não reconhecido. Use /help para ajuda.")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--autopull":
        ap_parser = argparse.ArgumentParser(prog="auto_commit_cli.py --autopull")
        ap_parser.add_argument("root_path", help="Diretório raiz com os repositórios")
        ap_parser.add_argument("--interval", type=int, default=60)
        ns = ap_parser.parse_args(sys.argv[2:])
        autopull_loop(
            root_path=normalize_repo_path(ns.root_path),
            interval_seconds=ns.interval,
        )
    else:
        repl()


if __name__ == "__main__":
    main()
