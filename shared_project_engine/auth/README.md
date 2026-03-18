# Shared FYERS Authentication Module

Unified authentication layer for ClawWork and fyersN7 projects.

## Quick Start

### 1. Setup credentials
```bash
cd /path/to/ClawWork_FyersN7
cp .env.example .env
# Edit .env with your FYERS_CLIENT_ID and FYERS_SECRET_KEY
```

### 2. Login (opens browser)
```bash
python -m shared_project_engine.auth.cli login
```

### 3. Verify
```bash
python -m shared_project_engine.auth.cli status
```

## Usage in Code

### Simple login
```python
from shared_project_engine.auth import quick_login
token = quick_login()
```

### API calls
```python
from shared_project_engine.auth import get_client

client = get_client()
quotes = client.quotes("NSE:NIFTY50-INDEX")
print(quotes)
```

### Full control
```python
from shared_project_engine.auth import FyersAuth, FyersClient, EnvManager

# Auth
auth = FyersAuth(
    client_id="YOUR_APP_ID",
    secret_key="YOUR_SECRET",
    insecure=True,  # For corporate networks
)
token = auth.login()

# Client
client = FyersClient()
profile = client.profile()
positions = client.positions()
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `login` | Interactive login (opens browser) |
| `login --redirected-url "..."` | Login with existing auth code |
| `login --insecure` | Login with SSL verification disabled |
| `status` | Check auth status and verify token |
| `url` | Print login URL only |
| `test` | Test API with quote request |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `FYERS_CLIENT_ID` | App ID from FYERS |
| `FYERS_SECRET_KEY` | Secret key from FYERS |
| `FYERS_REDIRECT_URI` | OAuth redirect (default: `http://127.0.0.1:8080/`) |
| `FYERS_ACCESS_TOKEN` | Access token (auto-saved after login) |

## File Structure

```
shared_project_engine/
  __init__.py          - Package root
  auth/
    __init__.py        - Package exports
    config.py          - Constants and defaults
    env_manager.py     - .env file handling
    fyers_auth.py      - OAuth login flow
    fyers_client.py    - REST API client
    cli.py             - Command-line interface
    README.md          - This file
```

## Integration with Projects

### From ClawWork
```python
import sys
sys.path.insert(0, "/path/to/ClawWork_FyersN7")
from shared_project_engine.auth import get_client
```

### From fyersN7
```python
import sys
sys.path.insert(0, "/path/to/ClawWork_FyersN7")
from shared_project_engine.auth import FyersAuth, get_client
```

Or use relative imports if running from within the project.
