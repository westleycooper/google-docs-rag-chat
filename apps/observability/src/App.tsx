/**
 * The observability app (ADR-0006).
 *
 * Its reason for existing beyond a status page: selecting a node shows the ADRs
 * that constrain it. "Why is this component like this?" is answered in the same
 * place as "is this component up?", which is what stops architecture
 * documentation being filed somewhere nobody looks.
 */

import { useEffect, useRef, useState } from 'react';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import {
  Box,
  Chip,
  CssBaseline,
  Divider,
  IconButton,
  Link,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  ThemeProvider,
  Tooltip,
  Typography,
  createTheme,
} from '@mui/material';
import { TopologyScene } from '@/components/TopologyScene';
import { fetchTopology, type ComponentNode, type NodeStatus, type Topology } from '@/api/topology';

// The chat app's dark preset -- Console (Dark) (see
// apps/frontend/src/theme.ts) -- duplicated by hex value rather than
// imported: the two apps are independent packages with no shared
// design-tokens package between them, and one flat file of colour constants
// is not worth introducing a workspace dependency for. Unlike the chat app,
// this page has no picker: it is a single-purpose live dashboard, not a
// surface where "make a choice" belongs, so it always renders in the
// product's dark look -- which means this block goes stale every time
// that preset changes and has to be updated by hand; there is no test that
// would catch the two drifting apart.
const theme = createTheme({
  palette: {
    mode: 'dark',
    background: { default: '#12181A', paper: '#1A2224' },
    primary: { main: '#5FA39D' },
    secondary: { main: '#7C93A0' },
    error: { main: '#E0685F' },
    warning: { main: '#D9A441' },
    success: { main: '#6FBF86' },
    info: { main: '#6FA8D9' },
    text: { primary: '#EDF2F1', secondary: '#9FB0B3' },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, sans-serif',
    button: { textTransform: 'none' },
  },
});

const STATUS_COLOUR: Record<NodeStatus, 'success' | 'warning' | 'error' | 'default'> = {
  ok: 'success',
  degraded: 'warning',
  down: 'error',
  unknown: 'default',
};

/** How often to poll. Fast enough to feel live, slow enough not to be the load. */
const POLL_MS = 3000;

export const App = () => {
  const [topology, setTopology] = useState<Topology | null>(null);
  const [selected, setSelected] = useState<ComponentNode | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  // Carries the previous topology across polls without re-running the effect.
  // A failed poll degrades the last known shape rather than blanking the
  // screen, which is exactly when an operator most needs to see something.
  const previous = useRef<Topology | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number;

    const poll = async () => {
      const next = await fetchTopology(previous.current);
      if (cancelled) return;
      previous.current = next;
      setTopology(next);
      setLastUpdate(new Date());
      timer = window.setTimeout(poll, POLL_MS);
    };
    void poll();

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const nodes = topology?.nodes ?? [];
  const unhealthy = nodes.filter((n) => n.status === 'down' || n.status === 'degraded');

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
        <Stack
          direction="row"
          spacing={2}
          alignItems="center"
          sx={{
            px: 3,
            py: 1.5,
            borderBottom: 1,
            borderColor: 'divider',
            bgcolor: 'background.paper',
          }}
        >
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            RAGDrive · Live Architecture
          </Typography>
          <Chip
            size="small"
            color={unhealthy.length === 0 ? 'success' : 'error'}
            label={
              unhealthy.length === 0
                ? 'all components healthy'
                : `${unhealthy.length} component${unhealthy.length === 1 ? '' : 's'} unhealthy`
            }
          />
          <Box sx={{ flex: 1 }} />
          <Typography variant="caption" color="text.secondary">
            {lastUpdate
              ? `updated ${lastUpdate.toLocaleTimeString()}`
              : 'connecting…'}
          </Typography>
        </Stack>

        <Box sx={{ flex: 1, display: 'flex', minHeight: 0 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <TopologyScene
              nodes={nodes}
              onSelect={setSelected}
              selectedId={selected?.id ?? null}
            />
          </Box>

          <Paper
            square
            sx={{
              width: 340,
              borderLeft: 1,
              borderColor: 'divider',
              overflowY: 'auto',
              p: 2,
            }}
          >
            {/* The accessible equivalent of the canvas, and the only route to
                the ADR list. The canvas is aria-hidden. */}
            <Typography variant="subtitle2" gutterBottom>
              Components
            </Typography>
            <List dense disablePadding>
              {nodes.map((node) => (
                <ListItem
                  key={node.id}
                  onClick={() => setSelected(node)}
                  sx={{
                    cursor: 'pointer',
                    borderRadius: 1,
                    bgcolor: selected?.id === node.id ? 'action.selected' : undefined,
                  }}
                  secondaryAction={
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      {node.url && (
                        <Tooltip title={`Open ${node.url}`}>
                          <IconButton
                            size="small"
                            component={Link}
                            href={node.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            sx={{ p: 0.25 }}
                          >
                            <OpenInNewIcon sx={{ fontSize: 14 }} />
                          </IconButton>
                        </Tooltip>
                      )}
                      <Chip
                        size="small"
                        color={node.checkable ? STATUS_COLOUR[node.status] : 'default'}
                        variant={node.checkable ? 'filled' : 'outlined'}
                        label={node.checkable ? node.status : 'reference'}
                        sx={{ height: 20, fontSize: 11 }}
                      />
                    </Stack>
                  }
                >
                  <ListItemText
                    primary={node.label}
                    secondary={
                      node.latency_ms !== null
                        ? `${node.kind} · ${node.latency_ms.toFixed(0)}ms`
                        : node.kind
                    }
                  />
                </ListItem>
              ))}
            </List>

            {selected && (
              <>
                <Divider sx={{ my: 2 }} />
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography variant="subtitle2">{selected.label}</Typography>
                  {selected.url && (
                    <Link
                      href={selected.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      variant="caption"
                      sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}
                    >
                      open <OpenInNewIcon sx={{ fontSize: 13 }} />
                    </Link>
                  )}
                </Stack>
                <Typography variant="caption" color="text.secondary" component="p">
                  {selected.checkable
                    ? selected.kind
                    : `${selected.kind} · reference only, no live status`}
                  {selected.checkable && ` · ${selected.status}`}
                  {selected.depends_on.length > 0 &&
                    ` · depends on ${selected.depends_on.join(', ')}`}
                </Typography>

                <Typography variant="subtitle2" sx={{ mt: 2 }}>
                  Decisions constraining this component
                </Typography>
                {selected.adr_refs.length === 0 ? (
                  <Typography variant="caption" color="text.secondary">
                    No ADR names this component yet.
                  </Typography>
                ) : (
                  <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                    {selected.adr_refs.map((ref) => (
                      <Link
                        key={ref}
                        href={`https://github.com/westleycooper/google-docs-rag-chat/tree/main/docs/adr`}
                        target="_blank"
                        rel="noopener noreferrer"
                        variant="caption"
                      >
                        {ref}
                      </Link>
                    ))}
                  </Stack>
                )}
              </>
            )}
          </Paper>
        </Box>
      </Box>
    </ThemeProvider>
  );
};