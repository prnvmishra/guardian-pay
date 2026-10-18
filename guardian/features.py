"""Feature definitions shared by the data generator, the trainer and the engine.

Two groups of signals feed every decision:

* sender side   - how the person is behaving while paying (captured by the app)
                  plus what the bank already knows about them
* receiver side - what the receiving account has been doing lately
                  (young account, many unrelated senders, money leaving fast)
"""

FEATURES = [
    # sender behaviour, captured live in the payment app
    "app_switches",            # times the user left the app during this payment
    "pasted_account",          # account number / UPI id was pasted, not typed or picked
    "confirm_hesitation_s",    # seconds spent on the final confirm screen
    "amount_edits",            # how many times the amount was changed
    "retries_after_limit",     # attempts after hitting a daily limit
    "session_duration_s",      # whole payment flow, open to confirm
    # sender context, already known to the bank
    "amount_ratio",            # amount / sender's 90 day average payment
    "balance_drain",           # amount / available balance (0..1)
    "new_payee",               # first payment to this account
    "payee_history",           # earlier payments to this payee
    "fd_broken_24h",           # fixed deposit closed early in the last 24h
    "night_hour",              # between 22:00 and 06:00
    "sender_age",
    # receiver side
    "recv_account_age_days",
    "recv_inbound_24h",        # credits received in the last 24h
    "recv_distinct_senders_24h",
    "recv_senior_share",       # share of those senders aged 60+
    "recv_forward_ratio",      # share of inbound money already sent onwards
]

# Values a calm, ordinary payment would have. Used to explain a score:
# we swap one signal back to its calm value and see how far the score falls.
CALM = {
    "app_switches": 0,
    "pasted_account": 0,
    "confirm_hesitation_s": 4,
    "amount_edits": 0,
    "retries_after_limit": 0,
    "session_duration_s": 35,
    "amount_ratio": 1.0,
    "balance_drain": 0.05,
    "new_payee": 0,
    "payee_history": 6,
    "fd_broken_24h": 0,
    "night_hour": 0,
    "sender_age": 40,
    "recv_account_age_days": 900,
    "recv_inbound_24h": 2,
    "recv_distinct_senders_24h": 2,
    "recv_senior_share": 0.1,
    "recv_forward_ratio": 0.1,
}

# sender_age is context, not something the person did, so it never shows up
# as a reason on its own.
NOT_A_REASON = {"sender_age", "payee_history"}


def _times(n):
    n = int(n)
    return "once" if n == 1 else f"{n} times"


def describe(name, value):
    """Plain-language reason shown to the bank analyst and the customer."""
    v = value
    if name == "app_switches":
        return f"Left the app {_times(v)} mid-payment", "Payment ke beech app chhod ke gaye"
    if name == "pasted_account":
        return "Account number was pasted from elsewhere", "Account number kahin se paste kiya"
    if name == "confirm_hesitation_s":
        return f"Paused {int(v)}s on the confirm screen", "Confirm se pehle kaafi der ruke"
    if name == "amount_edits":
        return f"Changed the amount {_times(v)}", "Amount baar-baar badla"
    if name == "retries_after_limit":
        return f"Retried {_times(v)} after hitting the limit", "Limit ke baad bhi baar-baar try kiya"
    if name == "session_duration_s":
        return f"Unusually long payment session ({int(v)}s)", "Payment mein bahut zyada time laga"
    if name == "amount_ratio":
        return f"Amount is {v:.0f}x the usual payment", f"Aam payment se {v:.0f} guna zyada"
    if name == "balance_drain":
        return f"Empties {v*100:.0f}% of available balance", f"Balance ka {v*100:.0f}% ek baar mein"
    if name == "new_payee":
        return "First ever payment to this account", "Is account mein pehli baar paisa"
    if name == "fd_broken_24h":
        return "Fixed deposit broken in the last 24 hours", "Pichhle 24 ghante mein FD todi"
    if name == "night_hour":
        return "Late-night payment", "Raat ke time payment"
    if name == "recv_account_age_days":
        return f"Receiving account is only {int(v)} days old", f"Paane wala account sirf {int(v)} din purana"
    if name == "recv_inbound_24h":
        return f"Receiver got {int(v)} credits in 24h", f"Paane wale ko 24 ghante mein {int(v)} payments"
    if name == "recv_distinct_senders_24h":
        return f"{int(v)} unrelated people paid this account today", f"Aaj {int(v)} alag logon ne paisa bheja"
    if name == "recv_senior_share":
        return f"{v*100:.0f}% of its senders are 60+", f"Bhejne walon mein {v*100:.0f}% buzurg"
    if name == "recv_forward_ratio":
        return f"Receiver moved {v*100:.0f}% of today's money onwards", "Aaya paisa turant aage bhej diya"
    return name, name
