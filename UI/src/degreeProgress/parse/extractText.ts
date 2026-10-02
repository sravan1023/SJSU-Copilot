// Browser-side PDF text extraction. pdf.js is loaded with a dynamic import so
// it gets its own chunk, and the worker is served from our own build (?url),
// never a CDN. The PDF bytes never leave the browser.
import type { TextLine } from "../types.ts";

const Y_TOLERANCE = 2.5; // PDF units; items within this share a line

export interface RawItem {
  str: string;
  x: number;
  y: number;
  width: number;
}

/** Group items into lines by y (tolerance), then sort each line by x. Pure. */
export function groupItems(page: number, raw: RawItem[]): TextLine[] {
  const items = raw.filter((i) => i.str.trim() !== "");
  items.sort((a, b) => b.y - a.y || a.x - b.x);
  const groups: { y: number; items: RawItem[] }[] = [];
  for (const it of items) {
    const g = groups.find((g) => Math.abs(g.y - it.y) <= Y_TOLERANCE);
    if (g) g.items.push(it);
    else groups.push({ y: it.y, items: [it] });
  }
  return groups.map((g) => {
    g.items.sort((a, b) => a.x - b.x);
    let text = "";
    let prevEnd: number | null = null;
    for (const it of g.items) {
      // pdf.js does not preserve fixed-width spacing; infer word gaps from x.
      if (prevEnd !== null && it.x - prevEnd > 1) text += " ";
      text += it.str;
      prevEnd = it.x + it.width;
    }
    return {
      page,
      y: g.y,
      items: g.items.map((i) => ({ str: i.str, x: i.x })),
      text: text.replace(/\s+/g, " ").trim(),
    };
  });
}

export async function extractLines(data: ArrayBuffer): Promise<TextLine[]> {
  const pdfjs = await import("pdfjs-dist");
  const workerUrl = (await import("pdfjs-dist/build/pdf.worker.min.mjs?url")).default;
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

  const task = pdfjs.getDocument({ data: new Uint8Array(data) });
  const doc = await task.promise;
  const lines: TextLine[] = [];
  try {
    for (let p = 1; p <= doc.numPages; p++) {
      const page = await doc.getPage(p);
      const content = await page.getTextContent();
      const raw: RawItem[] = [];
      for (const it of content.items) {
        if (!("str" in it)) continue;
        raw.push({ str: it.str, x: it.transform[4], y: it.transform[5], width: it.width });
      }
      lines.push(...groupItems(p, raw));
    }
  } finally {
    await task.destroy();
  }
  return lines;
}
