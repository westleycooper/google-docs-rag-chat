/**
 * Document source configuration (ADR-0003).
 *
 * The panel's real job is the skip list. A user needs to see what a run could
 * not read and why, because a silent skip is indistinguishable from an empty
 * folder — which is the failure that makes a RAG system quietly wrong.
 */

import { useState } from 'react';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import {
  useCreateSource,
  useDeleteSource,
  useGetLatestRun,
  useListSources,
  useSetSourceCredential,
  useStartIngestion,
} from '@/api/generated/sources/sources';
import type { SourceIn } from '@/api/generated/model';

const EMPTY: SourceIn = {
  name: '',
  provider: 'google_drive',
  auth_mode: 'service_account',
  principal: '',
  credential_ref: '',
  root_folder_ids: [],
  include_mime_types: [],
  exclude_mime_types: [],
  max_document_bytes: null,
  enabled: true,
};

const RunSummary = ({ sourceId }: { sourceId: string }) => {
  const { data: run } = useGetLatestRun(sourceId, {
    // Runs take minutes; polling is how the panel shows progress without a
    // second transport just for this.
    query: { refetchInterval: 5000 },
  });
  if (!run) return <Typography variant="caption">Never run</Typography>;

  const actionable = run.skips.filter((s) => s.actionable);

  return (
    <Stack spacing={0.5} sx={{ mt: 1 }}>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        <Chip size="small" label={run.state} sx={{ height: 20, fontSize: 11 }} />
        <Chip size="small" variant="outlined" label={`${run.ingested} ingested`} sx={{ height: 20, fontSize: 11 }} />
        <Chip size="small" variant="outlined" label={`${run.unchanged} unchanged`} sx={{ height: 20, fontSize: 11 }} />
        <Chip
          size="small"
          variant="outlined"
          color={run.skipped ? 'warning' : 'default'}
          label={`${run.skipped} skipped`}
          sx={{ height: 20, fontSize: 11 }}
        />
      </Stack>

      {!run.reconciled && (
        <Alert severity="warning" sx={{ py: 0 }}>
          {/* Discovered documents without a disposition means the corpus may be
              missing something with no skip record explaining why. */}
          {run.discovered} discovered but only{' '}
          {run.ingested + run.unchanged + run.skipped + run.failed} accounted for.
        </Alert>
      )}

      {run.error && <Alert severity="error" sx={{ py: 0 }}>{run.error}</Alert>}

      {actionable.length > 0 && (
        <Box>
          <Typography variant="caption" color="text.secondary">
            Could not read ({actionable.length}):
          </Typography>
          {actionable.slice(0, 5).map((skip) => (
            <Typography
              key={`${skip.external_id}-${skip.occurred_at}`}
              variant="caption"
              component="p"
              sx={{ color: 'text.disabled' }}
            >
              {skip.location} — {skip.reason.replace(/_/g, ' ')} for{' '}
              {skip.principal}
            </Typography>
          ))}
        </Box>
      )}
    </Stack>
  );
};

export const SourcesPanel = () => {
  const { data: sources = [], refetch } = useListSources();
  const create = useCreateSource();
  const remove = useDeleteSource();
  const ingest = useStartIngestion();
  const setCredential = useSetSourceCredential();

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<SourceIn>(EMPTY);
  const [secret, setSecret] = useState('');
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setError(null);
    try {
      const created = await create.mutateAsync({ data: draft });
      if (secret.trim()) {
        await setCredential.mutateAsync({
          sourceId: created.source_id,
          data: { secret },
        });
      }
      setOpen(false);
      setDraft(EMPTY);
      setSecret('');
      await refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not save the source');
    }
  };

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h6">Document sources</Typography>
        <Button startIcon={<AddIcon />} onClick={() => setOpen(true)}>
          Add source
        </Button>
      </Stack>

      {sources.length === 0 && (
        <Alert severity="info">
          No sources yet. Add a Google Drive to start ingesting documents.
        </Alert>
      )}

      {sources.map((source) => (
        <Paper key={source.source_id} variant="outlined" sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="subtitle2">{source.name}</Typography>
              <Typography variant="caption" color="text.secondary">
                {source.provider} · {source.auth_mode.replace(/_/g, ' ')} · as{' '}
                {source.principal}
              </Typography>
              <RunSummary sourceId={source.source_id} />
            </Box>
            <Stack direction="row">
              <IconButton
                aria-label={`Ingest ${source.name}`}
                onClick={() =>
                  ingest.mutate({ sourceId: source.source_id, params: {} })
                }
              >
                <PlayArrowIcon />
              </IconButton>
              <IconButton
                aria-label={`Delete ${source.name}`}
                onClick={async () => {
                  await remove.mutateAsync({ sourceId: source.source_id });
                  await refetch();
                }}
              >
                <DeleteOutlineIcon />
              </IconButton>
            </Stack>
          </Stack>
        </Paper>
      ))}

      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Add a document source</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label="Name"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              fullWidth
            />
            <TextField
              select
              label="Authentication"
              value={draft.auth_mode}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  auth_mode: e.target.value as SourceIn['auth_mode'],
                })
              }
              helperText="Service account needs a Workspace admin to grant domain-wide delegation. OAuth works for personal Gmail with no admin."
              fullWidth
            >
              <MenuItem value="service_account">
                Service account (domain-wide delegation)
              </MenuItem>
              <MenuItem value="oauth">OAuth (user consent)</MenuItem>
            </TextField>
            <TextField
              label="Principal"
              value={draft.principal}
              onChange={(e) => setDraft({ ...draft, principal: e.target.value })}
              helperText="The identity ingestion acts as. Defines the corpus boundary and appears on every skip record."
              fullWidth
            />
            <TextField
              label="Credential reference"
              value={draft.credential_ref}
              onChange={(e) =>
                setDraft({ ...draft, credential_ref: e.target.value })
              }
              helperText="An opaque handle, e.g. kms://ragoogle/sources/1"
              fullWidth
            />
            <TextField
              label="Credential"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              multiline
              minRows={3}
              type="password"
              helperText="Service-account JSON key, or {refresh_token, client_id, client_secret} for OAuth. Stored encrypted; never readable back."
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={save}
            disabled={!draft.name || !draft.principal || !draft.credential_ref}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};
