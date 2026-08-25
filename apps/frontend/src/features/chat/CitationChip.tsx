/**
 * One source, rendered with an icon for its type.
 *
 * The icon is the point: a user scanning an answer's sources recognises "three
 * Docs and a spreadsheet" far faster than they read four filenames.
 */

import ArticleIcon from '@mui/icons-material/Article';
import DescriptionIcon from '@mui/icons-material/Description';
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf';
import SlideshowIcon from '@mui/icons-material/Slideshow';
import TableChartIcon from '@mui/icons-material/TableChart';
import LaunchIcon from '@mui/icons-material/Launch';
import { Box, Chip, Link, Stack, Tooltip, Typography } from '@mui/material';
import type { CitationOut } from '@/api/generated/model';

const ICONS: Record<string, typeof ArticleIcon> = {
  'application/vnd.google-apps.document': DescriptionIcon,
  'application/vnd.google-apps.spreadsheet': TableChartIcon,
  'application/vnd.google-apps.presentation': SlideshowIcon,
  'application/pdf': PictureAsPdfIcon,
};

const iconFor = (mimeType: string) => {
  if (ICONS[mimeType]) return ICONS[mimeType];
  if (mimeType.startsWith('text/')) return ArticleIcon;
  return ArticleIcon;
};

interface Props {
  index: number;
  citation: CitationOut;
}

export const CitationChip = ({ index, citation }: Props) => {
  const Icon = iconFor(citation.mime_type);

  return (
    <Tooltip
      arrow
      placement="top"
      title={
        <Box sx={{ maxWidth: 360 }}>
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {citation.title} — {citation.location}
          </Typography>
          <Typography variant="caption" component="p" sx={{ mt: 0.5 }}>
            {citation.excerpt}
          </Typography>
          <Typography
            variant="caption"
            component="p"
            sx={{ mt: 0.5, opacity: 0.75 }}
          >
            {/* Provenance: found by both retrievers is a stronger signal than
                found by one, and ADR-0004 makes that visible rather than
                collapsing it into a single score. */}
            relevance {(citation.relevance * 100).toFixed(0)}% · found by{' '}
            {citation.found_by.join(' + ')}
          </Typography>
        </Box>
      }
    >
      <Chip
        size="small"
        icon={<Icon fontSize="small" />}
        label={
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="caption" sx={{ fontWeight: 600 }}>
              [{index}]
            </Typography>
            <Typography variant="caption" noWrap sx={{ maxWidth: 180 }}>
              {citation.title}
            </Typography>
            {citation.web_url && (
              <Link
                href={citation.web_url}
                target="_blank"
                rel="noopener noreferrer"
                sx={{ display: 'flex' }}
                aria-label={`Open ${citation.title} in a new tab`}
              >
                <LaunchIcon sx={{ fontSize: 12 }} />
              </Link>
            )}
          </Stack>
        }
        variant="outlined"
        sx={{ maxWidth: 280 }}
      />
    </Tooltip>
  );
};
