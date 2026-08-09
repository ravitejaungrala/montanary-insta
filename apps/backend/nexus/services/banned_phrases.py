"""Cold-email cliche detection.

A list of overused, hollow phrases that immediately signal automated /
template outreach. Used by the personalization pipeline to flag drafts
before they're sent.
"""

from typing import List

BANNED_PHRASES: List[str] = [
    # The classics
    "leverage",
    "leveraging",
    "synergy",
    "synergies",
    "synergize",
    "game-changer",
    "game changer",
    "game-changing",
    "disruptive",
    "disrupt",
    "disruption",
    "innovative",
    "innovate",
    "innovation",
    "revolutionise",
    "revolutionize",
    "revolutionary",
    "transform",
    "transformative",
    "transformation",
    "next-generation",
    "next generation",
    "cutting-edge",
    "cutting edge",
    "best-in-class",
    "best in class",
    "world-class",
    "world class",
    "industry-leading",
    "industry leading",
    "thought leader",
    "thought leadership",
    "value-add",
    "value add",
    "low-hanging fruit",
    "move the needle",
    "circle back",
    "touch base",
    "ping you",
    "quick chat",
    "quick call",
    "jump on a call",
    "hop on a call",
    "pick your brain",
    "i hope this email finds you well",
    "hope this finds you well",
    "i hope this finds you well",
    "trust this email finds you well",
    "to whom it may concern",
    "as per my last email",
    "circling back",
    "just checking in",
    "following up",
    "bumping this",
    "paradigm shift",
    "paradigm",
    "deep dive",
    "deep-dive",
    "boil the ocean",
    "win-win",
    "win win",
    "no-brainer",
    "no brainer",
    "low hanging fruit",
    "scalable solution",
    "robust solution",
    "seamless",
    "seamlessly",
    "frictionless",
    "holistic",
    "end-to-end",
    "end to end",
    "ai-powered",
    "ai powered",
    "powered by ai",
    "10x",
    "10-x",
    "supercharge",
    "supercharged",
    "skyrocket",
    "unleash",
    "unlock the power",
    "unlock potential",
    "empower",
    "empowering",
    "best practices",
    "core competency",
    "core competencies",
    "bandwidth",
    "actionable insights",
    "data-driven",
    "data driven",
    "results-driven",
    "results driven",
    "needle-moving",
    "mission-critical",
    "mission critical",
    "let me know if",
    "let me know your thoughts",
    "looking forward to hearing from you",
    "looking forward to your response",
    "any thoughts",
    "would love to chat",
    "would love to connect",
    "would love to hear",
    "thoughts?",
]


def validate_email_body(text: str) -> List[str]:
    """Return the list of banned phrases found (case-insensitive) in `text`.

    Returns an empty list when the body is clean.
    """
    if not text:
        return []
    lower = text.lower()
    hits: List[str] = []
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            hits.append(phrase)
    return hits
