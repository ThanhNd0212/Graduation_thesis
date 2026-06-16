"""Stateful LEGO-shop chatbot (hybrid mode).

Pipeline: NLU (intent + NER) -> slot-filling state -> product confirmation -> reply.
See ../chatbot_demo_plan.md. Run the CLI demo from the project root:

    python -m chatbot.cli

Mode `llm_full` + cost metrics come last (Nhánh C).
"""
