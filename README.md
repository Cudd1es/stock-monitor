# Stock Monitor Agent 

A LangGraph-powered stock monitoring system that:
- Parses user requirements with LLMs
- Fetches live prices and news via yfinance
- Judges price movement thresholds
- Summarizes results using LLMs
- Sends notifications to Discord or console

```mermaid
flowchart LR
    S[supervisor (entry)] -->|route| P[parse]
    S -->|route| PR[price]
    S -->|route| N[news]
    S -->|route| J[judge]
    S -->|route| B[brief]
    S -->|route| NO[notify]
    S -->|route| END((END))

    P -->|route| P
    P -->|route| PR
    P -->|route| N
    P -->|route| J
    P -->|route| B
    P -->|route| NO
    P -->|route| END

    PR -->|route| P
    PR -->|route| PR
    PR -->|route| N
    PR -->|route| J
    PR -->|route| B
    PR -->|route| NO
    PR -->|route| END

    N -->|route| P
    N -->|route| PR
    N -->|route| N
    N -->|route| J
    N -->|route| B
    N -->|route| NO
    N -->|route| END

    J -->|route| P
    J -->|route| PR
    J -->|route| N
    J -->|route| J
    J -->|route| B
    J -->|route| NO
    J -->|route| END

    B -->|route| P
    B -->|route| PR
    B -->|route| N
    B -->|route| J
    B -->|route| B
    B -->|route| NO
    B -->|route| END

    NO -->|route| P
    NO -->|route| PR
    NO -->|route| N
    NO -->|route| J
    NO -->|route| B
    NO -->|route| NO
    NO -->|route| END
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Add your API key
Create a `.env` file in the project root:
```
OPENAI_API_KEY=sk-your-key-here
```

### 3. Configure Discord (optional)
Create a `config.yaml`:
```yaml
discord:
  webhook_url: "https://discord.com/api/webhooks/xxxx/xxxx",
    mention_id: "xxxxxxxx"
```

---

## Usage
Run the main entry:
```bash
python main.py
```

Example prompt (inside `main.py`):
```python
init = {
    "requirement": "Check MSFT and META price and tell me in discord"
}
```

---

## Project Structure
```
main.py             # LangGraph workflow definition
parser.py           # Parse natural language into structured rules
ticker_checker.py   # Fetch stock prices and compute change
news_collector.py   # Get recent headlines from yfinance
llm_interaction.py  # Interface with OpenAI models
notifier.py         # Send Discord or console notifications
```

---

## Powered By
- [LangGraph](https://python.langchain.com/docs/langgraph)
- [yfinance](https://github.com/ranaroussi/yfinance)
- [OpenAI API](https://platform.openai.com)

---

## License
Apache 2.0

