import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/cn';

interface MarkdownContentProps {
  content: string;
  className?: string;
}

const REMARK_PLUGINS = [remarkGfm];

/**
 * Rendu Markdown GFM sécurisé et stylé pour les messages, cartes, sorties de routines et notes.
 *
 * @param props.content - Texte Markdown brut.
 * @param props.className - Classes CSS additionnelles.
 */
export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={cn('jarvis-markdown', className)}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{content}</ReactMarkdown>
    </div>
  );
}
