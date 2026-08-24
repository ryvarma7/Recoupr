"""Recovery message rendering.

Hard content rules (enforced downstream by the guardrail gate's content scan):
never request an OTP, card number, PIN, or any credential; always reference the
real order ID and amount; the only action is tapping a Razorpay-hosted link.
"""

from __future__ import annotations

from app.models.entities import Case

_TEMPLATES: dict[tuple[str, str], str] = {
    # (language, tone) → body template. {order} and {amount} are always real values.
    ("en", "formal"): (
        "Dear customer, your payment of {amount} for order {order} could not be completed. "
        "You can complete it securely here: {link}"
    ),
    ("en", "friendly"): (
        "Hi! Your payment of {amount} for order {order} didn't go through. "
        "No worries — you can retry securely here: {link}"
    ),
    ("en", "urgent"): (
        "Your payment of {amount} for order {order} failed and is on hold. "
        "Complete it within 24 hours to keep your order: {link}"
    ),
    ("hi", "formal"): (
        "Namaste, aapka {amount} ka bhugtan order {order} ke liye poora nahi ho saka. "
        "Ise yahan surakshit roop se complete karein: {link}"
    ),
    ("hi", "friendly"): (
        "Namaste! Aapka {amount} ka payment order {order} ke liye fail ho gaya. "
        "Tension mat leejiye — yahan se dobara try kar sakte hain: {link}"
    ),
    ("hi", "urgent"): (
        "Kripya dhyan dein: order {order} ka {amount} payment fail ho gaya hai. "
        "Order banaye rakhne ke liye 24 ghante mein yahan complete karein: {link}"
    ),
    ("hinglish", "formal"): (
        "Hello, aapka {amount} ka payment order {order} ke liye complete nahi hua tha. "
        "Aap ise securely complete kar sakte hain yahan: {link}"
    ),
    ("hinglish", "friendly"): (
        "Hey! Order {order} ka {amount} payment process nahi hua. "
        "Koi baat nahi — yahan se securely pay kar sakte hain: {link}"
    ),
    ("hinglish", "urgent"): (
        "Order {order} ka {amount} payment fail ho gaya hai — order hold pe hai. "
        "24 hours mein complete kar dijiye: {link}"
    ),
}


def format_amount(amount_paise: int, currency: str = "INR") -> str:
    if currency != "INR":
        return f"{amount_paise / 100:.2f} {currency}"
    rupees = amount_paise / 100
    return f"₹{rupees:,.0f}" if rupees == int(rupees) else f"₹{rupees:,.2f}"


def render_recovery_message(
    *,
    case: Case,
    language: str,
    tone: str,
    short_url: str,
) -> str:
    safe_tone = tone if tone in ("formal", "friendly", "urgent") else "friendly"
    key = (language if (language, safe_tone) in _TEMPLATES else "en", safe_tone)
    body = _TEMPLATES[key].format(
        amount=format_amount(case.amount),
        order=case.order_id or case.subscription_id or "your purchase",
        link=short_url,
    )
    # Defensive guarantee independent of templates: no credential-requesting words ever render.
    banned = ("otp", "pin ", "cvv", "card number")
    assert not any(word in body.lower() for word in banned), "template leaked sensitive-content phrasing"
    return body
