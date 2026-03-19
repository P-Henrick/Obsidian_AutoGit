# Contribuindo com Obsidian AutoGit

## Fluxo recomendado

1. Abra uma issue descrevendo problema ou melhoria
2. Crie uma branch com nome claro
3. Faça commits pequenos e objetivos
4. Abra um Pull Request com contexto e teste manual

## Padrões

- Mantenha compatibilidade com Windows
- Preserve nome do produto: Obsidian AutoGit
- Evite dependências sem necessidade
- Não comite dados locais sensíveis em arquivos JSON

## Testes manuais mínimos

1. Abrir GUI sem erro
2. Executar um ciclo de fetch em pelo menos um repositório
3. Testar commit manual em repositório de teste
4. Validar comandos básicos da CLI

## Commit messages

Use formato simples e consistente:

- `feat: ...`
- `fix: ...`
- `docs: ...`
- `chore: ...`
