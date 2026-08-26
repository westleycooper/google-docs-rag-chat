/**
 * The context budget panel: the 3D meter plus its accessible equivalent.
 *
 * The table is not a fallback — it is the primary affordance, and the canvas is
 * the enhancement. A WebGL canvas is unreachable by a screen reader and
 * unusable by keyboard, so every action available in the meter is available
 * here, with the same eviction-frontier signal.
 */

import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import LockIcon from '@mui/icons-material/Lock';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import {
  Alert,
  Box,
  Chip,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import { ContextMeter } from './ContextMeter';
import { contextItemDropped } from '@/store/sessionSlice';
import { useAppDispatch, useAppSelector } from '@/store';

export const ContextPanel = () => {
  const theme = useTheme();
  const dispatch = useAppDispatch();
  const budget = useAppSelector((s) => s.session.budget);
  const degraded = useAppSelector((s) => s.session.degraded);

  if (!budget) {
    return (
      <Paper sx={{ p: 2 }} variant="outlined">
        <Typography variant="body2" color="text.secondary">
          Ask a question to see how the context window is being used.
        </Typography>
      </Paper>
    );
  }

  const atRisk = budget.items.filter((i) => i.evicts_next);
  const percent = Math.min(budget.utilisation * 100, 100);

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" justifyContent="space-between" alignItems="baseline">
          <Typography variant="subtitle2">Context window</Typography>
          <Typography variant="caption" color="text.secondary">
            {budget.used_tokens.toLocaleString()} /{' '}
            {budget.available_tokens.toLocaleString()} tokens
          </Typography>
        </Stack>

        <LinearProgress
          variant="determinate"
          value={percent}
          color={budget.over_budget ? 'error' : percent > 85 ? 'warning' : 'primary'}
          sx={{ height: 6, borderRadius: 3 }}
        />

        {atRisk.length > 0 && (
          <Alert
            severity="warning"
            icon={<WarningAmberIcon fontSize="small" />}
            sx={{ py: 0 }}
          >
            {/* The whole reason ADR-0008 exists: name what is about to be lost
                while there is still time to protect it. */}
            {atRisk.length} item{atRisk.length === 1 ? '' : 's'} will be dropped
            on the next turn unless you free space.
          </Alert>
        )}

        {degraded.map((note) => (
          <Alert key={note} severity="info" sx={{ py: 0 }}>
            {note}
          </Alert>
        ))}

        <ContextMeter
          budget={budget}
          onSelect={(itemId) => dispatch(contextItemDropped(itemId))}
        />

        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {budget.segments
            .filter((s) => s.token_count > 0)
            .map((segment) => (
              <Chip
                key={segment.context_class}
                size="small"
                label={`${segment.context_class} ${segment.token_count.toLocaleString()}`}
                sx={{
                  bgcolor:
                    theme.contextColours[
                      segment.context_class as keyof typeof theme.contextColours
                    ],
                  color: '#fff',
                  height: 20,
                  fontSize: 11,
                }}
              />
            ))}
        </Stack>

        <Box sx={{ maxHeight: 260, overflowY: 'auto' }}>
          <Table size="small" aria-label="Context window contents">
            <TableHead>
              <TableRow>
                <TableCell>Item</TableCell>
                <TableCell align="right">Tokens</TableCell>
                <TableCell align="right">Action</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {budget.items.map((item) => {
                const locked = item.context_class === 'system';
                return (
                  <TableRow
                    key={item.item_id}
                    sx={{
                      bgcolor: item.evicts_next
                        ? 'action.hover'
                        : undefined,
                    }}
                  >
                    <TableCell sx={{ maxWidth: 220 }}>
                      <Typography variant="caption" noWrap display="block">
                        {item.label}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {item.context_class}
                        {item.evicts_next ? ' · evicts next' : ''}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="caption">
                        {item.token_count.toLocaleString()}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {locked ? (
                        <Tooltip title="System context cannot be dropped">
                          <LockIcon fontSize="small" color="disabled" />
                        </Tooltip>
                      ) : (
                        <Tooltip title={`Drop ${item.label}`}>
                          <IconButton
                            size="small"
                            aria-label={`Drop ${item.label} from context`}
                            onClick={() =>
                              dispatch(contextItemDropped(item.item_id))
                            }
                          >
                            <DeleteOutlineIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Box>
      </Stack>
    </Paper>
  );
};
