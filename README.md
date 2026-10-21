# Stock Monitor Agent 

A LangGraph-powered stock monitoring system that trace stock price, 
collect news and generate reports with workflow mode and agent mode.



### Workflow Mode
- Parses user requirements with LLMs
- Fetches live prices and news via yfinance
- Judges price movement thresholds
- Summarizes results using LLMs
- Sends notifications to Discord or console

Topology: 
```mermaid
flowchart TD
  %% Topology of the LangGraph workflow

  S[supervisor]
  P[parse]
  PR[price]
  N[news]
  J[judge]
  B[brief]
  NO[notify]
  E([END])

  %% Conditional routing from supervisor based on state.plan
  S -- route(state) → parse --> P
  S -- route(state) → price --> PR
  S -- route(state) → news --> N
  S -- route(state) → judge --> J
  S -- route(state) → brief --> B
  S -- route(state) → notify --> NO
  S -- route(state) → __END__ --> E

  %% All business nodes return to supervisor
  P --> S
  PR --> S
  N --> S
  J --> S
  B --> S
  NO --> S
```

### Agent Mode

- LLM agent parses user prompt itself
- LLM agent make use of tools provided to finish works from user requirements
- LLM agent decides when to end

Topology:

![agent_topology.png](readme_asserts/agent_topology.png "agent topology")


## Example

User prompt:

```
Check MSFT and META price and give me English report in discord
```

Result:
![discord.png](readme_asserts/discord.png "discord message")
---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Add your API key
Create a `.env` file in the project root with your [OpenRouter](https://openrouter.ai) key:
```
OPENROUTER_API_KEY=sk-or-your-key-here
# optional, defaults to minimax/minimax-m3.1-flash:free
LLM_MODEL=minimax/minimax-m3.1-flash:free
```

### 3. Configure Discord (optional)
Create a `config.yaml`:
```yaml
discord:
  webhook_url: "https://discord.com/api/webhooks/xxxx/xxxx"
  mention_id: "xxxxxxxx"
```

---

## Usage

### Use Workflow Mode
Run the main entry:
```bash
python main_workflow.py
```

Example prompt (inside `main_workflow.py`):
```python
init = {
    "requirement": "Check MSFT and META price and tell me in discord"
}
```


### Use Agent Mode

Run the main entry:
```bash
python main_agent.py
```

Example prompt (inside `main_agent.py`):
```python
init = {
    "requirement": "分析 MSFT, NVDA 和 META, 并在discord给我发简报"
}
```


---

## Project Structure
```
main_workflow.py    # LangGraph workflow definition
main_agent.py       # LangGraph agent definition
core.py             # Shared logic for both modes: prices, context, brief, config
parser.py           # Parse natural language into structured rules
ticker_checker.py   # Fetch stock prices and compute change
news_collector.py   # Get recent headlines from yfinance
llm_interaction.py  # Interface with LLMs via OpenRouter (OpenAI-compatible API)
notifier.py         # Send Discord or console notifications
tests/              # pytest unit tests
```

---

## Acknowledgements
- [LangGraph](https://python.langchain.com/docs/langgraph)
- [yfinance](https://github.com/ranaroussi/yfinance)
- [OpenRouter](https://openrouter.ai)

---

## License
Apache 2.0

