# Wingman Wizard

One agent file, `agents/wingman_wizard_agent.py`, that drives Wingman through
its own `wingman-cli`. Serve it with `rappmcp serve agents`.

## Which wingman-cli

`$WINGMAN_CLI` if set (it must be a program this Mac can run, or the agent
says so). Otherwise the installed `Wingman.app`, or the newest build under
`~/Library/Developer/Wingman`, preferring one that can type, key, paste and
click. If only an older Wingman is found, those actions say to update it.
If none is found, or Wingman is not running: "Open Wingman on this Mac".

## Guardrails, in code

- There is no sign-in, account, sync, takeover, update or reset action.
- Text that looks like a sign-in, an authorization, a password, a code or a
  payment is refused before anything runs. So is a code or password typed a
  few characters at a time, or key by key. That record lives on disk
  (`$WINGMAN_WIZARD_STATE`, else `$RAPPMCP_DATA_DIR/wingman-wizard`), since
  each call is a new process.
- Keys that paste a clipboard (`cmd+v`, `ctrl+v`, in any order or case, with
  any other modifiers) are refused. Use `computer_paste` with the text.
- An answer is checked against every option it could pick: by number, by
  letter, by words or by value, in every step. A question id is matched in
  any case; an id `read` does not show is refused.
- While any bot has an open help request ("Action needed"), type, paste,
  key, click and done are refused: "This step is yours: finish it in
  Wingman's window." The person's Done in Wingman clears it. The check reads
  every bot's whole conversation, since the computer is shared, and fails
  closed when it cannot.
- A send or answer waits up to 45 seconds, and always inside the server's
  timeout. If the bot is still answering, it says so and says not to send
  again.

## The lock is a stopgap

`wingman-cli` has no read-only way to ask Wingman's own waiting state
(`BoxRequests.waiting`). So a request the computer's watch stream announced
before any conversation row exists is not seen by this agent. The
authoritative lock belongs in Wingman's control socket later: refuse type,
paste, key, click and done there while a request waits. This agent-side
check is the stopgap until then.

## Testing

The tests never touch the real Wingman or Grok Bot's cloud. They put
`tests/fixtures/fake-wingman-cli` in `$WINGMAN_CLI`. It records each argv
and answers from a plan.
