import React from "react";

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "quote"; text: string };

function inline(text: string): React.ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|~~[^~]+~~|\*[^*]+\*|_[^_]+_|\x60[^\x60]+\x60|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  return text.split(pattern).filter(Boolean).map((part, index) => {
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("~~") && part.endsWith("~~")) {
      return <del key={index}>{part.slice(2, -2)}</del>;
    }
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return part;
  });
}

export function MarkdownText({ text }: { text: string }) {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let ordered: boolean | null = null;

  const flushParagraph = () => {
    const value = paragraph.join(" ").trim();
    if (value) blocks.push({ type: "paragraph", text: value });
    paragraph = [];
  };
  const flushList = () => {
    if (list.length && ordered !== null) blocks.push({ type: "list", ordered, items: list });
    list = [];
    ordered = null;
  };

  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*$/);
    const boldHeading = line.match(/^\*\*(.{2,100})\*\*:?$/);
    const quote = line.match(/^>\s?(.*)$/);
    const number = line.match(/^\s*\d+[.)]\s+(.+)$/);
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
    if (heading || boldHeading) {
      flushParagraph(); flushList();
      blocks.push({ type: "heading", level: heading ? Math.min(heading[1].length, 4) : 3, text: heading ? heading[2] : boldHeading![1].replace(/:$/, "") });
      continue;
    }
    if (quote) {
      flushParagraph(); flushList();
      blocks.push({ type: "quote", text: quote[1] });
      continue;
    }
    if (number || bullet) {
      flushParagraph();
      const isOrdered = Boolean(number);
      if (ordered !== null && ordered !== isOrdered) flushList();
      ordered = isOrdered;
      list.push((number ?? bullet)![1]);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();

  return <div className="markdown-text">
    {blocks.map((block, index) => {
      if (block.type === "heading") {
        const Tag = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
        return <Tag key={index}>{inline(block.text)}</Tag>;
      }
      if (block.type === "list") {
        const Tag = block.ordered ? "ol" : "ul";
        return <Tag key={index}>{block.items.map((item, i) => <li key={i}>{inline(item)}</li>)}</Tag>;
      }
      if (block.type === "quote") return <blockquote key={index}>{inline(block.text)}</blockquote>;
      return <p key={index}>{inline(block.text)}</p>;
    })}
  </div>;
}
