# Project instructions

Read `AGENT_HANDOFF.md` before implementing changes. This is a local MEDHA PS 8 prototype, not a diagnosis service.

- Preserve the working offline simulation and explicitly distinguish simulated and live measurements.
- Keep hardware inference independent of the GPU training server.
- Never claim hardware or clinical validation from simulation tests.
- Do not put secrets, raw participant recordings, session data or model weights into version control.
- Run relevant tests after measurement or API changes.
- Update `AGENT_HANDOFF.md` at the end of each implementation stage with actual changes, verification, unresolved issues and the next actions. The user explicitly requested continuity across agents.
