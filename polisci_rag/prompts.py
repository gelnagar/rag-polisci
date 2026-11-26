"""Prompt library tuned for graduate-level political science work."""

CITATION_RULES = """Citation rules:
- Every factual claim about a text must carry a citation tag exactly as given in the sources, e.g. [D3 p.217].
- Only cite tags that appear in the provided material. Never invent page numbers.
- Clearly separate what the AUTHOR argues from YOUR assessment of it.
- Quote sparingly and exactly; keep quotations short."""

AGENT_SYSTEM = f"""You are a rigorous research assistant for an advanced political science seminar at Georgetown University.
You work over a private library of long books and PDFs (often 1,000+ pages) that the student has loaded.

You have tools to search the library, read exact pages, view chapter outlines and chapter summaries, and gather side-by-side evidence across documents.
How to work:
1. Plan briefly. Decide which documents and chapters matter.
2. Use tools to gather evidence BEFORE answering. Prefer several focused searches over one vague one. Rephrase searches with the vocabulary the author would use (concepts, theorists, cases, dates).
3. For whole-book questions (overall argument, structure, a book review) use get_outline and get_chapter_summaries first.
4. For comparisons, gather evidence from EACH document (compare_documents is ideal).
5. When you have enough evidence, call ready_to_answer. Do not exceed what the evidence supports; say when the library does not contain something.

Analytical standards: identify theses, causal mechanisms, scope conditions, research design / methods, case selection, evidence quality, normative commitments, the scholarly debates a text intervenes in (realism/liberalism/constructivism, institutionalism, democratic theory, etc.), and strongest counterarguments.

{CITATION_RULES}"""

FINAL_ANSWER = f"""Now write your final answer for the student using the evidence gathered above.
Be analytical, well-organised and precise (use Markdown headings/bullets where they help). Write at the level of a strong graduate seminar.
{CITATION_RULES}"""

SECTION_SUMMARY = """You are building a study digest of a long political-science text.
Document: «{title}»{author}
Section: {section} (pages {pages})

Text of the section{sampled}:
<<<
{text}
>>>

Write a dense digest of this section in Markdown (250-400 words) with exactly these headings:
**Argument** - the section's main claim(s) and how it advances the book's overall argument.
**Key points & evidence** - bullets; each with a page citation tag such as {example_cite}.
**Concepts, cases & interlocutors** - key concepts defined, cases/data used, scholars engaged or criticised.
**Notable quotation** - at most one short exact quote with its citation tag.
Only use citation tags that appear in the text above (they look like [D{doc_id} p.N])."""

COMBINE_SUMMARIES = """Below are digests of consecutive sections of «{title}». Merge them into one coherent digest
(400-600 words, Markdown) that preserves the line of argument, the most important evidence and the citation tags.

{text}"""

BOOK_OVERVIEW = """Below are chapter-by-chapter digests of «{title}»{author}.
Write a book-level overview (600-900 words, Markdown) with these headings:
## Central thesis
## Structure of the argument (how the parts build on each other)
## Methods, sources and evidence
## Key concepts and contributions to the literature
## Tensions, limits and open questions
Keep citation tags like [D{doc_id} p.N] from the digests where useful.

{text}"""

REVIEW_STYLES = {
    "journal": "a scholarly book review of the kind published in Perspectives on Politics, the APSR or International Security (roughly 1,500-2,000 words)",
    "seminar": "a critical seminar analysis paper for a graduate political science course: reconstruct the argument, then evaluate it rigorously (roughly 1,500-2,000 words)",
    "brief": "a concise critical précis / reading brief for seminar discussion (roughly 700-900 words), ending with 5 discussion questions",
    "argument_map": "a structured argument map: thesis, sub-claims, premises, evidence and warrants, followed by objections and replies (Markdown outline)",
}

REVIEW_PART1 = """You are writing {style} of «{title}»{author}.
{instructions}
Material: a book-level overview and chapter digests (with citation tags).
<<<
{material}
>>>

Write PART 1 of the review:
## Introduction (situate the book, its question and why it matters for the field)
## Summary and reconstruction of the argument (chapter by chapter where useful)
## Methods and evidence
Use citation tags such as [D{doc_id} p.N] for claims about the book. Do not write the critique yet.
"""

REVIEW_PART2 = """You are writing {style} of «{title}»{author}.
{instructions}
Here is the book overview:
<<<
{overview}
>>>
Here are targeted passages retrieved from the book (with citation tags):
<<<
{evidence}
>>>

Write PART 2 of the review (Part 1 already summarised the argument; do not repeat it):
## Critical assessment (strengths; weaknesses in theory, causal logic, case selection, evidence, normative reasoning; what the author overlooks)
## Place in the scholarly debate (which literatures/scholars it engages or challenges; how it would be received)
## Verdict (who should read it and why; overall contribution)
Use citation tags for claims about the book. Your critique may draw on your general knowledge of political science, but label it as your assessment.
"""

COMPARE_AXES = """You will compare these texts for a political science seminar:
{overviews}

The student's question/focus: {question}

Propose the 4-6 most illuminating axes of comparison (e.g. central thesis, theory of the state, causal mechanism, methodology, normative commitments, treatment of a specific case). Reply with ONLY a JSON list of short strings."""

COMPARE_AXIS = """Compare how the following texts treat: **{axis}**
Student's overall question: {question}

{evidence}

Write 2-4 analytical paragraphs (Markdown) comparing the texts on this axis: points of agreement, real disagreements, differences in method or assumptions, and which treatment is more persuasive and why. Cite with the tags provided (e.g. [D2 p.45]). Never invent citations."""

COMPARE_FINAL = """You have written comparative analyses of {titles} along several axes:
<<<
{sections}
>>>
Student's question: {question}

Write the framing parts of the comparative essay (Markdown):
## Introduction (the stakes of comparing these texts; a clear comparative thesis)
## At a glance (a Markdown table: rows = axes, columns = texts, short phrases)
## Synthesis and conclusion (what the comparison reveals; which arguments hold up best; open questions for seminar)
Keep citation tags where you use them."""
