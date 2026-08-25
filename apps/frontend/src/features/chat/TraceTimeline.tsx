/**
 * The retrieval trace for one turn (ADR-0009).
 *
 * A linear run renders as a timeline; that is the common case and does not need
 * 3D. The `branched` signal — a repeated stage or a re-query — is what escalates
 * to the graph view, which is ADR-0009's "where necessary" taken literally.
 *
 * `rejected` is shown, not just `selected`. Seeing that the right document was
 * retrieved and then ranked eighth is the single most diagnostic signal
 * available when an answer is wrong, and it is invisible in any
 * citations-only design.
 */

import { useState } from 'react';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  LinearProgress,
  Stack,
  Typography,
} from '@mui/material';
import type { TraceEventOut } from '@/api/generated/model';

interface Props {
  trace: TraceEventOut[];
  streaming: boolean;
  branched: boolean;
}

export const TraceTimeline = ({ trace, streaming, branched }: Props) => {
  const [open, setOpen] = useState(false);
  if (trace.length === 0 && !streaming) return null;

  const total = trace.reduce((sum, e) => sum + e.duration_ms, 0);
  const slowest = trace.reduce(
    (worst, e) => (e.duration_ms > (worst?.duration_ms ?? -1) ? e : worst),
    undefined as TraceEventOut | undefined,
  );

  return (
    <Accordion
      expanded={open}
      onChange={(_, value) => setOpen(value)}
      disableGutters
      sx={{ bgcolor: 'transparent', '&::before': { display: 'none' } }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography variant="caption" color="text.secondary">
            {trace.length} step{trace.length === 1 ? '' : 's'} ·{' '}
            {total.toFixed(0)}ms
          </Typography>
          {branched && (
            <Chip
              size="small"
              label="multi-step"
              color="secondary"
              variant="outlined"
              sx={{ height: 18, fontSize: 10 }}
            />
          )}
          {streaming && (
            <Box sx={{ width: 60 }}>
              <LinearProgress sx={{ height: 2 }} />
            </Box>
          )}
        </Stack>
      </AccordionSummary>

      <AccordionDetails sx={{ pt: 0 }}>
        <Stack spacing={1}>
          {trace.map((event, i) => (
            <Stack
              key={`${event.stage}-${i}`}
              direction="row"
              spacing={1.5}
              alignItems="flex-start"
            >
              <Box
                sx={{
                  mt: 0.6,
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  flexShrink: 0,
                  bgcolor:
                    event === slowest && total > 0
                      ? 'warning.main'
                      : 'success.main',
                }}
              />
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Stack direction="row" spacing={1} alignItems="baseline">
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {event.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {event.duration_ms.toFixed(0)}ms
                  </Typography>
                </Stack>
                <Typography variant="caption" color="text.secondary">
                  {event.summary}
                </Typography>
                {event.rejected.length > 0 && (
                  <Typography
                    variant="caption"
                    component="p"
                    sx={{ color: 'text.disabled' }}
                  >
                    considered {event.considered}, discarded{' '}
                    {event.rejected.length}
                  </Typography>
                )}
              </Box>
            </Stack>
          ))}
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
};
