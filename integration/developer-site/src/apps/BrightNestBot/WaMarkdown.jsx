import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";

/**
 * BrightNest answers are authored for WhatsApp: **bold**, *bold*, _italic_,
 * ~strike~, and "- " bullets (sometimes jammed onto one line by the model).
 * Normalize lightly, then render as Markdown.
 */
function asText(value) {
  if (value == null || value === false) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(asText).join("");
  return "";
}

export function preprocessWaMarkdown(raw) {
  let text = asText(raw).replace(/\r\n/g, "\n").trim();
  if (!text) return "";

  // Collapse JSX indentation whitespace inside a single paragraph
  text = text.replace(/[ \t]*\n[ \t]+/g, "\n");

  // Line-leading bullets (WhatsApp / demo booking copy)
  text = text.replace(/^•\s+/gm, "- ");

  // WhatsApp ~strike~ → GFM ~~strike~~
  text = text.replace(/~(?!~)([^~\n]+?)~(?!~)/g, "~~$1~~");

  // WhatsApp *bold* → **bold** (skip already-doubled **)
  text = text.replace(/(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)/g, "**$1**");

  // "…experience. - **Lead…" → real list lines
  text = text.replace(/([^\n])[ \t]+[-•][ \t]+(?=\*\*)/g, "$1\n- ");
  text = text.replace(/([.:;!?…])[ \t]+[-•][ \t]+(?=[A-Z0-9“"‘'])/g, "$1\n- ");

  // Blank line before a list that follows prose on the prior line
  text = text.replace(/([^\n])\n(-\s+)/g, "$1\n\n$2");

  return text;
}

export default function WaMarkdown({ children, className = "bn-md" }) {
  const source = useMemo(() => preprocessWaMarkdown(children), [children]);

  if (!source) return null;

  return (
    <div className={className}>
      <ReactMarkdown
        components={{
          a: ({ href, children: linkChildren }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {linkChildren}
            </a>
          ),
          // Keep chat bubbles compact — drop accidental raw HTML
          p: ({ children: pChildren }) => <p>{pChildren}</p>,
          ul: ({ children: ulChildren }) => <ul>{ulChildren}</ul>,
          ol: ({ children: olChildren }) => <ol>{olChildren}</ol>,
          li: ({ children: liChildren }) => <li>{liChildren}</li>,
          strong: ({ children: sChildren }) => <strong>{sChildren}</strong>,
          em: ({ children: eChildren }) => <em>{eChildren}</em>,
          del: ({ children: dChildren }) => <del>{dChildren}</del>,
          code: ({ children: cChildren }) => <code>{cChildren}</code>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
