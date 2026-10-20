import type { ReactNode } from 'react'

import { Texte } from './ui'

/**
 * Rend un récap (petit sous-ensemble de Markdown) sans jamais injecter de HTML:
 * le texte vient d'un fichier ou d'un LLM, donc il reste une donnée non fiable.
 * Gère `##`/`###`, listes `-` et `1.`, citations `>` (pièges), **gras**, `code`
 * et les formules `$...$` (KaTeX, via `Texte`).
 */
function enLigne(texte: string): ReactNode[] {
  return texte.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((m, i) => {
    if (m.startsWith('**') && m.endsWith('**') && m.length > 4) {
      return (
        <strong key={i} className="font-semibold text-texte">
          <Texte>{m.slice(2, -2)}</Texte>
        </strong>
      )
    }
    if (m.startsWith('`') && m.endsWith('`') && m.length > 2) {
      return (
        <code key={i} className="rounded bg-surface px-1.5 py-0.5 font-mono text-[0.9em]">
          {m.slice(1, -1)}
        </code>
      )
    }
    return m ? <Texte key={i}>{m}</Texte> : null
  })
}

type Bloc =
  | { genre: 'titre'; niveau: number; texte: string }
  | { genre: 'liste'; ordonnee: boolean; items: string[] }
  | { genre: 'piege'; lignes: string[] }
  | { genre: 'para'; texte: string }

function blocs(markdown: string): Bloc[] {
  const sortie: Bloc[] = []
  for (const brute of markdown.replace(/\r/g, '').split('\n')) {
    const ligne = brute.trimEnd()
    const dernier = sortie[sortie.length - 1]
    let m: RegExpMatchArray | null
    if (!ligne.trim()) {
      sortie.push({ genre: 'para', texte: '' })
    } else if ((m = ligne.match(/^(#{1,4})\s+(.*)$/))) {
      sortie.push({ genre: 'titre', niveau: m[1].length, texte: m[2] })
    } else if ((m = ligne.match(/^\s*[-*]\s+(.*)$/))) {
      if (dernier?.genre === 'liste' && !dernier.ordonnee) dernier.items.push(m[1])
      else sortie.push({ genre: 'liste', ordonnee: false, items: [m[1]] })
    } else if ((m = ligne.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (dernier?.genre === 'liste' && dernier.ordonnee) dernier.items.push(m[1])
      else sortie.push({ genre: 'liste', ordonnee: true, items: [m[1]] })
    } else if ((m = ligne.match(/^>\s?(.*)$/))) {
      if (dernier?.genre === 'piege') dernier.lignes.push(m[1])
      else sortie.push({ genre: 'piege', lignes: [m[1]] })
    } else if (dernier?.genre === 'para' && dernier.texte) {
      dernier.texte += ` ${ligne.trim()}`
    } else {
      sortie.push({ genre: 'para', texte: ligne.trim() })
    }
  }
  return sortie.filter((b) => b.genre !== 'para' || b.texte)
}

export function Recap({ texte }: { texte: string }) {
  return (
    <div className="space-y-4 text-[0.95rem] leading-relaxed text-texte-doux">
      {blocs(texte).map((b, i) => {
        if (b.genre === 'titre') {
          return b.niveau <= 2 ? (
            <h2 key={i} className="pt-3 text-lg font-semibold text-texte">
              {enLigne(b.texte)}
            </h2>
          ) : (
            <h3 key={i} className="pt-1 text-sm font-semibold uppercase tracking-wide text-accent">
              {enLigne(b.texte)}
            </h3>
          )
        }
        if (b.genre === 'liste') {
          const Balise = b.ordonnee ? 'ol' : 'ul'
          return (
            <Balise
              key={i}
              className={`space-y-1.5 pl-5 ${b.ordonnee ? 'list-decimal' : 'list-disc'} marker:text-accent`}
            >
              {b.items.map((it, j) => (
                <li key={j}>{enLigne(it)}</li>
              ))}
            </Balise>
          )
        }
        if (b.genre === 'piege') {
          return (
            <div
              key={i}
              className="space-y-1 rounded-xl border border-difficile/40 bg-difficile/10 px-3.5 py-3 text-difficile"
            >
              {b.lignes.map((l, j) => (
                <p key={j}>{enLigne(l)}</p>
              ))}
            </div>
          )
        }
        return <p key={i}>{enLigne(b.texte)}</p>
      })}
    </div>
  )
}
