---
name: cellertz-critic-explanation
description: "Verbalize a resolved GroundedCriticResult. Do not decide rank."
version: 1.0.0
---

# Cellertz grounded critic explanation

Use when the input is already a CriticExplanationRequest / GroundedCriticResult.

The scientific state is resolved. Do not call tools.

## Rules

1. Explain the supplied assessment.
2. Do not recalculate any scientific field.
3. Do not recommend rank changes.
4. Do not add scientific claims absent from grounded_context.

Do not recommend promotion, demotion, or deprioritization.
Do not invent a composite, a probability, or experimental efficacy.
Cite `knowledge_used` when `grounded_context.status` is available.
If `unsupported_terms` lists a token, write the token as given. Do not expand it.

## Unavailable context

If `grounded_context.status` is `unavailable`, explain only the
`critic_assessment` and say that no additional validated scientific
context is available.
