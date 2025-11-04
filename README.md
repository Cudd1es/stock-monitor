# stock-monitor


```mermaid
flowchart LR
    subgraph User
      U[human input<br/>(natural-language prompt)]
    end

    subgraph Agent Graph
      SUP[supervisor<br/>(plan or next)]
      PARSER[parser<br/>(NL -> rules)]
      PRICE[get ticker price]
      NEWS[get news]
      JUDGE[judge change vs threshold]
      BRIEF[summarize<br/>(LLM)]
      PERSIST[persist to SQLite]
      ALERT[alert decision]
      NOTIFY[discord/console notification]
    end

    U --> SUP

    %% supervisor decides actions
    SUP --> PARSER
    PARSER --> SUP

    SUP --> PRICE
    PRICE --> SUP

    SUP --> NEWS
    NEWS --> SUP

    SUP --> JUDGE
    JUDGE --> SUP

    SUP --> BRIEF
    BRIEF --> SUP

    SUP --> PERSIST
    PERSIST --> SUP

    %% supervisor can trigger alert or final notify
    SUP --> ALERT
    ALERT --> NOTIFY

    %% daily/interval report
    SUP --> NOTIFY

    %% data/state flows back to supervisor for next decisions
```
