# LLM Debate System

A multi-LLM consensus engine where two AI models debate code changes until they reach agreement.

## Features

- **Multi-LLM Debate**: Claude (Anthropic) and GPT (OpenAI) debate in real-time
- **Auto Model Switching**: Automatically uses cheaper models for simple tasks, powerful models for complex ones
- **Real-time Streaming**: WebSocket-based live updates as LLMs debate
- **Consensus Detection**: Automatically detects when both LLMs agree
- **Project Context**: Reads your codebase to provide relevant context
- **Token Tracking**: Monitor token usage across the debate

## Architecture

```
┌─────────────────┐     WebSocket      ┌─────────────────┐
│    Frontend     │ ◄──────────────►   │    Backend      │
│  (React + Vite) │                    │   (FastAPI)     │
│   Port: 3002    │                    │   Port: 8080    │
└─────────────────┘                    └────────┬────────┘
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    │                           │                           │
                    ▼                           ▼                           ▼
            ┌───────────────┐           ┌───────────────┐           ┌───────────────┐
            │  Anthropic    │           │    OpenAI     │           │ Model Router  │
            │  (Claude)     │           │    (GPT)      │           │ (Auto-switch) │
            └───────────────┘           └───────────────┘           └───────────────┘
```

## Quick Start

### 1. Start the System

```bash
cd llm_debate
chmod +x start.sh
./start.sh
```

### 2. Open the UI

Navigate to: http://localhost:3002

### 3. Configure API Keys

Enter your API keys in the UI:
- **Anthropic**: Get from https://console.anthropic.com/
- **OpenAI**: Get from https://platform.openai.com/

### 4. Start a Debate

1. Select which LLM is the **Proposer** (makes suggestions)
2. Select which LLM is the **Critic** (reviews and challenges)
3. Enter your project path
4. Describe the task or question
5. Click "Start Debate"

## Manual Setup

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/configure` | POST | Configure API keys |
| `/api/status` | GET | Check configured providers |
| `/api/debate/start` | POST | Start a new debate session |
| `/api/debate/{id}` | GET | Get session summary |
| `/api/debate/{id}/messages` | GET | Get all messages |
| `/ws/debate/{id}` | WS | Real-time debate stream |

## Model Auto-Switching

The system automatically selects models based on task complexity:

| Complexity | Anthropic Model | OpenAI Model |
|------------|-----------------|--------------|
| Simple | claude-haiku-4-5 | gpt-4o-mini |
| Medium | claude-sonnet-4 | gpt-4o |
| Complex | claude-opus-4 | gpt-4o |

**Escalation**: If consensus isn't reached after 3 rounds, the system escalates to more powerful models.

## Debate Flow

```
Round 1:
  PROPOSER → Makes initial proposal
  CRITIC   → Reviews, raises concerns or approves

Round 2 (if not approved):
  PROPOSER → Addresses concerns, refines proposal
  CRITIC   → Reviews again

... continues until:
  - CONSENSUS: Critic approves
  - DEADLOCK: Max rounds reached
```

## Configuration

### Environment Variables (Optional)

Instead of entering keys in the UI, you can set:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
```

### Max Rounds

Adjustable in the UI (default: 7). Higher values allow more thorough debates but use more tokens.

## Token Usage

Typical debate:
- Simple task: ~2,000-5,000 tokens
- Medium task: ~5,000-15,000 tokens
- Complex task: ~15,000-40,000 tokens

## Troubleshooting

**"API key not configured"**
- Ensure you've entered valid API keys and clicked "Configure Keys"

**"Project path not found"**
- Enter the full absolute path (e.g., `/Users/yourname/project`)

**WebSocket connection failed**
- Make sure the backend is running on port 8080
- Check browser console for errors

## License

MIT
