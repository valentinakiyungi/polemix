# 🤖 Local AI Debate Simulator

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-orange.svg)](https://github.com/langchain-ai/langgraph)
[![LM Studio](https://img.shields.io/badge/LM_Studio-100%25_Offline-green.svg)](https://lmstudio.ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An autonomous multi-agent debate and synthesis simulator built using **LangGraph**, **LangChain**, and **LM Studio**. 

It pits opposing AI personas—an extreme technological optimist and a skeptical systems architect—against each other in automated turn-taking rounds, followed by an objective moderator synthesis. Best of all, it runs **100% offline and locally** on your own hardware.

---

## ✨ Features

* **100% Offline & Private:** Operates entirely locally via LM Studio without external API leakage or data privacy concerns.
* **Multi-Agent State Routing:** Utilizes LangGraph conditional edges and circular workflows to manage conversational turn-taking between opposing personas seamlessly.
* **Automated Moderator Synthesis:** Concludes each debate cycle by summarizing core trade-offs, areas of consensus, and key arguments.

---

## 🛠️ Tech Stack

* **Orchestration:** LangGraph, LangChain, `langchain-openai`
* **Runtime:** Python 3.9+ / Virtual Environment (`venv`)
* **Local Inference:** LM Studio (OpenAI-compatible local server)

---

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone [https://github.com/Valentina14142000/local-debate-simulator.git](https://github.com/Valentina14142000/local-debate-simulator.git)
cd local-debate-simulator
```

### 2. Setup Environment

Create and activate a Python virtual environment, then install the required dependencies:

```Bash
python3 -m venv venv
source venv/bin/activate
pip install langgraph langchain langchain-openai pydantic
```

### 3. Start LM Studio

Open LM Studio on your local machine.

Load your preferred local model (e.g., Llama 3, Mistral, or Phi-3).

Start the Local Server (usually runs on http://localhost:1234/v1).

### 4. Configure Environment Variables

Create a .env file in the root directory to point LangChain to your local inference engine:

```Code snippet
OPENAI_API_BASE=http://localhost:1234/v1
OPENAI_API_KEY=not-needed-locally
```

### 5. Run the Simulation

Execute the main script to kick off the multi-agent debate:

```Bash
python main.py
```



