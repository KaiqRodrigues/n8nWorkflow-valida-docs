# Validação Automatizada de Documentos Fiscais

## Contexto

O conferência de documentações fiscais que devem ser encaminhadas mensalmente pelos prestadores de serviço contratados pela Prefeitura é manual e visual. A verificação inicial é simples: data de validade do documento, periodo de apuração(referente o mês anterior ao serviço prestado) e confirmação do CNPJ correto.
Toda documentação é recebida por email, considerando então a quantidade de doumentações e a quantidade de empresas que prestam serviço, a simples conferencia de informações acontece de forma mecanica sucetivel a erros.

## Fluxo

Pipeline dual de extração (texto direto + Vision AI como fallback), integração com Gemini para classificação e extração estruturada via prompt engenhado, parsing defensivo de respostas da IA, validação automática de vigência, e output em Google Sheets via OAuth2. Decisões de engenharia documentadas com justificativas técnicas e roadmap de evolução.

## Arquitetura

O projeto é composto por dois serviços via Docker Compose:

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Compose                       │
│                                                         │
│  ┌──────────────┐         ┌────────────────────┐        │
│  │ imap-service │◄────────│       n8n          │        │
│  │ (Flask API)  │ HTTP    │   (workflow engine)│        │
│  │  porta 5000  │ Request │    porta 5678      │        │
│  └──────┬───────┘         └─────────┬──────────┘        │
│         │                           │                   │
└─────────┼───────────────────────────┼───────────────────┘
          │                           │
    ┌─────▼─────┐              ┌──────▼───────┐
    │  Servidor │              │ Google Sheets│
    │   IMAP    │              │ Gemini API   │
    └───────────┘              └──────────────┘
```

**imap-service** — serviço Python (Flask) que conecta ao servidor IMAP via imaplib, busca emails não lidos (UNSEEN), extrai anexos, converte para base64 e retorna os dados em JSON via endpoint REST (GET /buscar-emails). Cada anexo é retornado como um item individual com remetente, assunto, data, nome do arquivo, MIME type e conteúdo em base64.

**n8n** — executa o workflow de classificação e validação. Consome o endpoint do imap-service via Schedule Trigger + HTTP Request.

### Separação do serivço de conexão IMAP do trigger

A conexão do node IMAP Trigger do n8n por algum motivo caia mesmo com o "Force Reconnection" ativado, pesquisando descobri que possui um bug documentado onde a conexão com o servidor cai silenciosamente e o workflow não dispara, mesmo com a opção Force Reconnection ativada. O node funciona em execução manual, onde eu precisava executar o node após ou durante o envio do email de teste que eu fazia, mas essa situação não condiz com uma automação.

A alternativa seria usar Schedule Trigger + um node de leitura IMAP, mas o n8n não oferece um node IMAP de leitura como ação (separado do trigger).

A solução adotada foi externalizar a leitura de emails para um microserviço Flask rodando em container separado. O n8n chama esse serviço via HTTP Request em intervalos regulares (Schedule Trigger). Isso contorna o bug do IMAP Trigger, o sandbox do Code node, e dá controle total sobre a conexão IMAP.

### Fluxo do workflow

```
Schedule Trigger (intervalo configurável)
  │
  ▼
HTTP Request → imap-service (GET /buscar-emails)
  │
  ▼
Extrai texto do PDF
  │
  ▼
Texto extraído? ──[não]──▶ Converte p/ base64 → Gemini Vision API (HTTP Request)
  │ [sim]                                              │
  ▼                                                    ▼
Gemini Flash (LLM Chain)                    Processa resposta da IA
  │                                                    │
  └───────────────► Parsing (JSON) ◄───────────────────┘
                        │
                        ▼
                  É documento fiscal?
                    │ [sim]
                    ▼
              Valida datas (válido / vencido / vence em breve)
                    │
                    ▼
              Google Sheets (append)
```

## Decisões de engenharia

A extração direta de texto pega os caracteres exatos embutidos no PDF. Um CNPJ como 111.222.333/0001-22 sai exatamente assim, sem interpretação. Já o Vision AI lê a partir da imagem renderizada do documento e pode errar com caracteres (0 vs O, 1 vs l), números pequenos, ou texto parcialmente coberto por marca d'água — como a CND Estadual da PGE-SP, onde o CNPJ foi lido como 777222333 em vez de 111222333 por sobreposição do brasão do estado. O erro do OCR, não pode ser aceito nessa validação.

O pipeline mantém texto puro quando disponível (maioria das guias e CNDs federais) e aciona Vision AI apenas como fallback para documentos que não possuem texto selecionável (PDFs renderizados como imagem). Ambos os caminhos convergem para o mesmo node de Parsing e seguem o fluxo idêntico dali em diante.

### Escolha do modelo

| Critério | Gemini 2.5 Flash | Claude (Anthropic) | GPT-4o (OpenAI) | Ollama (local) |
|----------|-----------------|-------------------|-----------------|----------------|
| Custo | Gratuito (free tier) | Mínimo $5 | Requer pagamento | Gratuito |
| Multimodal | Sim (texto + imagem) | Sim | Sim | Depende do modelo |
| Qualidade | Suficiente | Superior | Superior | Inferior em modelos pequenos |
| Privacidade | Dados podem ser usados para treino (free tier) | Dados não usados para treino | Dados não usados para treino | Total (local) |
| Hardware | Nenhum | Nenhum | Nenhum | GPU 24GB+ para modelos competitivos |

O Gemini Flash foi escolhido por ser o único modelo com capacidade multimodal (necessária para o fallback de Vision AI) disponível em free tier sem cartão de crédito. Para um projeto de portfólio testado com dados fictícios, a questão de privacidade do free tier não se aplica.

Para produção com dados reais, seria recomendado migrar para um paid tier (onde os dados não são utilizados para treinamento) ou para um modelo local robusto (ex: Qwen 27B+ via Ollama), garantindo privacidade dos dados fiscais dos prestadores.

### Por que HTTP Request direto na API do Gemini (caminho Vision) em vez de outro LLM Chain?

O Basic LLM Chain do n8n trabalha com entrada de texto. Para enviar uma imagem (documento renderizado) ao Gemini, é necessário incluir o binário em base64 no corpo da requisição como inline_data — funcionalidade que o LLM Chain não expõe. O HTTP Request permite montar o payload exato da API do Gemini com o documento em base64 e o prompt na mesma requisição.

## Descrição dos nodes

**Schedule Trigger** — dispara o workflow em intervalos configuráveis (ex: a cada 30 minutos).

**HTTP Request (imap-service)** — chama `http://imap-service:5000/buscar-emails` e recebe os anexos em JSON com base64. Substitui os nodes IMAP Trigger + Se tem anexo + Separa em n items da versão anterior.

**Extrai o texto** — Extract from File (operação PDF). Converte o PDF em texto plano. Consome o binário principal mas preserva o backup.

**Se existe texto** — condicional que verifica se a extração retornou conteúdo (`text.trim().length > 0`). Documentos com texto embutido seguem pelo caminho principal; documentos renderizados como imagem seguem pelo fallback de Vision AI.

**Analise do documento - LLM** — Basic LLM Chain conectado ao Google Gemini 2.5 Flash (temperature 0). Recebe o texto extraído e retorna um JSON estruturado com tipo do documento, CNPJ, razão social, datas e valor.

**Transforma em binário** — Code node que recupera o backup do binário via `getBinaryDataBuffer()` e converte para base64, preparando o documento para envio à API de Vision.

**Requisita a IA** — HTTP Request direto à API do Gemini (`generateContent`), enviando o documento como imagem inline (base64) com o prompt de classificação e extração.

**Processa resposta da IA** — Code node que extrai o texto da resposta da API do Gemini (estrutura `candidates[0].content.parts[0].text`) e trata falhas com fallback para `PARSE_FALHA`.

**Parsing** — Code node que recebe a saída de ambos os caminhos (LLM Chain e Vision AI), faz `JSON.parse()` da resposta da IA, remove wrappers de markdown (` ```json `) e trata JSON inválido.

**Se é um Doc Fiscal** — condicional que verifica o campo `ignorar`. Documentos classificados como "OUTRO" ou com falha de parsing são descartados.

**Analise da validade** — Code node que compara `data_validade` com a data atual e classifica: `VALIDO`, `VENCE_EM_BREVE` (≤30 dias) ou `VENCIDO`. Calcula os dias restantes.

**Output** — Google Sheets (append row). Registra o resultado final com as colunas: `cnpj`, `razao_social`, `tipo`, `data_emissao`, `data_validade`, `valor`, `status`, `dias_restantes`, `data_verificacao`.

## Tratamento de PDFs sem texto selecionável

Alguns documentos governamentais, como a CND Estadual da PGE-SP, são gerados como imagem com marca d'água e brasão sobrepostos ao texto, impossibilitando a extração direta. O workflow utiliza Vision AI como fallback, porém com margem de erro em campos parcialmente cobertos por elementos gráficos. Essa limitação não é específica do Gemini — qualquer modelo de Vision (GPT-4o, Claude) teria dificuldade com documentos onde marcas d'água sobrepõem o texto.

### Alternativas para evolução futura

1. **Validação direta nos portais governamentais** - abordagem mais robusta. Com o CNPJ extraído de outros documentos do mesmo prestador, consultar os portais dos órgãos emissores (Receita Federal, PGE, Caixa/FGTS, TST) via HTTP Request ou RPA para validar a situação fiscal na fonte, eliminando a dependência de OCR. É a estratégia adotada por soluções de mercado como Dootax e projetos open source como Automacao-de-CND.

2. **OCR dedicado para documentos brasileiros** - ferramentas como Crow Docs (open source, offline, otimizado para setor público brasileiro) ou o community node n8n-nodes-tesseractjs podem oferecer melhor precisão que o Vision AI generalista em documentos com layout complexo.

3. **Vision AI com flag de confiança** - manter a abordagem atual adicionando ao prompt a instrução para a IA sinalizar campos com leitura incerta (`confianca: baixa`), direcionando esses registros para conferência manual na planilha.

## Configuração

### Pré-requisitos

- Docker e Docker Compose
- API key do [Google AI Studio](https://aistudio.google.com) (Gemini)
- Conta Google para Google Sheets

### Estrutura do projeto

```
validacao-fiscal/
├── docker-compose.yml
├── .env
├── .gitignore
├── README.md
├── workflow.json
└── imap-service/
    ├── Dockerfile
    ├── requirements.txt
    └── app.py
```

### 1. Clone e configure

```bash
git clone https://github.com/seu-usuario/validacao-fiscal.git
cd validacao-fiscal
```

Crie o arquivo `.env` na raiz com suas credenciais:

```
IMAP_HOST=email-ssl.com.br
IMAP_USER=seu@email.com
IMAP_PASS=sua_senha
```

### 2. Suba os serviços

```bash
docker compose up --build
```

O n8n estará disponível em `http://localhost:5678` e o imap-service em `http://localhost:5000`.

Para testar o imap-service isoladamente:

```bash
curl http://localhost:5000/buscar-emails
```

Se retornar `[]` (lista vazia sem erro), a conexão IMAP está funcionando.

### 3. Importe o workflow

No n8n, importe o arquivo `workflow.json` e recrie as credentials (Gemini API key, Google Sheets OAuth2) — elas não são exportadas no JSON por segurança.

### 4. Gemini API

Acesse [aistudio.google.com](https://aistudio.google.com), gere uma API key e insira no node "Requisita a IA" (HTTP Request) substituindo o placeholder na URL.

**Importante:** nunca commite a API key no repositório.

### 5. Google Sheets

O workflow utiliza o Google Sheets como destino dos dados extraídos. Para conectar o n8n à sua conta Google, é necessário criar credenciais OAuth2 no [Google Cloud Console](https://console.cloud.google.com):

1. Crie um projeto e ative a **Google Sheets API**
2. Configure a tela de consentimento OAuth (tipo Externo)
3. Gere um **Client ID** e **Client Secret** (tipo Aplicativo da Web)
4. No URI de redirecionamento, utilize o callback do seu n8n (ex: `http://localhost:5678/rest/oauth2-credential/callback`)
5. Insira as credenciais no node do Google Sheets e autorize o acesso

Crie uma planilha com os seguintes cabeçalhos na primeira linha:

```
cnpj | razao_social | tipo | data_emissao | data_validade | valor | status | dias_restantes | data_verificacao
```

### Exemplo de output

| cnpj | razao_social | tipo | data_emissao | data_validade | valor | status | dias_restantes | data_verificacao |
|------|-------------|------|-------------|--------------|-------|--------|---------------|-----------------|
| 12.345.678/0001-90 | EMPRESA EXEMPLO LTDA | CND_FGTS | 2026-04-04 | 2026-05-03 | — | VENCIDO | -58 | 2026-06-30 |
| 12.345.678/0001-90 | EMPRESA EXEMPLO LTDA | GUIA_INSS | 2026-04-01 | 2026-05-20 | 1507.53 | VENCIDO | -41 | 2026-06-30 |

## Limitações conhecidas

- **Vision AI com margem de erro** — documentos com marca d'água pesada (ex: CND PGE-SP) podem ter campos extraídos incorretamente. O CNPJ é o campo mais suscetível.
- **Limites do free tier** — o Gemini Flash no free tier tem limites de requisições por dia que variam por projeto e região. Pode ocorrer erro 429 durante testes intensivos.
- **Sem processamento de imagens avulsas** — comprovantes de pagamento em formato JPG/PNG recebidos como anexo ainda não são processados pelo pipeline atual.

## Implementações futuras

- **Processamento de imagens** - suporte a comprovantes de pagamento em JPG/PNG via o mesmo caminho de Vision AI já implementado.

- **Cruzamento guia × comprovante** - validação automática comparando valor da guia de imposto com valor do comprovante de pagamento, agrupados por CNPJ.

- **Checklist de pendências** - aba na planilha com documentos esperados por prestador, identificando automaticamente o que está faltando.

- **Validação humana + resposta automática** - coluna de aprovação na planilha para revisão humana antes do disparo de email de resposta ao prestador via SMTP (email-ssl.com.br, porta 465, SSL).

- **Validação de emails** - O fluxo atual não conta com tratamentos se o emial não possui anexo ou se o anexo não é fiscal. Essa validação poderia abrir uma arvore de possibilidades.

## Ressalva Técnina

O free tier do Gemini Flash utilizado possui limites de requisições por dia que variam por projeto e região, podendo ser tão baixos quanto 20 RPD. Para uso contínuo ou testes iterativos, recomenda-se migrar para um paid tier ou utilizar um modelo local com hardware compatível (GPU 24GB+ para modelos competitivos como Qwen 27B). O pipeline está funcional e testado, mas a quota do free tier pode não comportar volume de produção.
Tendo em vista também que alguns documentos governamentais constam com marca dágua e brasão no fundo do texto, mesmo com o OCR, não é garantido a leitura e extração correta dos caracteres, dificuldade identificada ao testar com a CND da Procuradoira Geral da União.

## Privacidade e dados

Este projeto foi desenvolvido e testado exclusivamente com **dados fictícios**. Nenhum documento fiscal real de prestadores foi utilizado nos testes ou armazenado no repositório.

Para uso em produção com dados reais, recomenda-se utilizar o Gemini em paid tier (dados não são usados para treinamento) ou modelo local, avaliar conformidade com LGPD caso documentos contenham dados de pessoa física (CPF, nome), e verificar políticas internas da organização sobre envio de dados fiscais para APIs externas.

## Stack

- [n8n](https://n8n.io) (self-hosted, Docker)
- [Google Gemini 2.5 Flash](https://ai.google.dev) (classificação e extração via IA)
- [Google Sheets](https://sheets.google.com) (output)
- [Flask](https://flask.palletsprojects.com) / Python (imap-service)
- [Docker Compose](https://docs.docker.com/compose/) (orquestração)
- JavaScript (Code nodes do n8n)
