# Obsidian AutoGit

Aplicativo para automatizar fluxo de Git em múltiplos repositórios, com interface gráfica e CLI.

## O que ele faz

- Auto-Pull periódico em vários repositórios
- Commit e push em lotes para reduzir risco em mudanças grandes
- Inclusão manual de repositórios fora da raiz escaneada
- Apelidos para facilitar identificação dos projetos
- Executável para Windows via PyInstaller

## Tecnologias

- Python 3.10+
- Tkinter (GUI)
- Git instalado no sistema
- PyInstaller (build do executável)

## Requisitos

1. Git instalado e disponível no PATH
2. Python 3.10 ou superior

## Instalação (modo desenvolvimento)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Execução

### GUI

```powershell
python autogit_gui.py
```

### CLI interativo

```powershell
python auto_commit_cli.py
```

### Comandos da CLI

- `/autopull "C:\Repos" --interval 60`
- `/commit "C:\MeuRepo" "mensagem" "descrição opcional" --batch-size 100 --delay 5`
- `/addrepo "C:\OutroRepo"`
- `/removerepo "C:\OutroRepo"`
- `/listrepos`

## Build de executável (Windows)

```powershell
.\build_exe.ps1
```

Saída esperada: `dist\Obsidian AutoGit.exe`

## Organização dos arquivos de estado

- `repos_aliases.json`: apelidos dos repositórios
- `repos_extra.json`: repositórios adicionados manualmente

No repositório público, esses arquivos começam vazios por privacidade.

## Publicação no GitHub

Checklist rápido:

1. Configure descrição e tópicos do repositório
2. Adicione screenshot da interface na seção de assets
3. Crie release com o executável em `dist/`
4. Mantenha changelog por versão

## Licença

Este projeto está sob a licença MIT. Consulte `LICENSE`.
