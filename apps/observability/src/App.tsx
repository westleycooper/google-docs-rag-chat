/**
 * The observability app (ADR-0006).
 *
 * Its reason for existing beyond a status page: selecting a node shows the ADRs
 * that constrain it. "Why is this component like this?" is answered in the same
 * place as "is this component up?", which is what stops architecture
 * documentation being filed somewhere nobody looks.
 */

import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Chip,
  CssBaseline,
  Divider,
  Link,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  ThemeProvider,
  Typography,
  createTheme,
} from '@mui/material';
import { TopologyScene } from '@/components/TopologyScene';
import { fetchTopology, type ComponentNode, type NodeStatus, type Topology } from '@/api/topology';

// The same Wes Anderson family as the chat app's dark mode (see
// apps/frontend/src/theme.ts) -- velvet maroon rather than navy-black, dusty
// teal and mustard rather than indigo. Duplicated by hex value rather than
// imported: the two apps are independent packages with no shared design-tokens
// package between them, and one flat file of colour constants is not worth
// introducing a workspace dependency for.
const theme = createTheme({
  palette: {
    mode: 'dark',
    background: { default: '#241417', paper: '#331C20' },
    primary: { main: '#6FB3AC' },
    secondary: { main: '#C08A2E' },
    error: { main: '#D2603A' },
    warning: { main: '#E3B23C' },
    success: { main: '#8FBF7A' },
    text: { primary: '#F2E8D5', secondary: '#C9B79E' },
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
          sx={{ px: 3, py: 1.5, borderBottom: 1, borderColor: 'divider' }}
        >
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            RAGDrive · Architecture
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
                    <Chip
                      size="small"
                      color={STATUS_COLOUR[node.status]}
                      label={node.status}
                      sx={{ height: 20, fontSize: 11 }}
                    />
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
                <Typography variant="subtitle2">{selected.label}</Typography>
                <Typography variant="caption" color="text.secondary" component="p">
                  {selected.kind} · {selected.status}
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