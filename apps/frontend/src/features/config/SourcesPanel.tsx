/**
 * Document source configuration (ADR-0003, ADR-0016).
 *
 * The panel's real job is the skip list. A user needs to see what a run could
 * not read and why, because a silent skip is indistinguishable from an empty
 * folder — which is the failure that makes a RAG system quietly wrong.
 *
 * Credentials are never typed in by hand as an ID: `credential_ref` is always
 * server-generated, filled in either by completing OAuth ("Connect Google
 * Drive") or by storing a pasted service-account key. A hand-typed reference
 * is one typo from silently pointing a source at nothing.
 */

import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditIcon from '@mui/icons-material/Edit';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import GoogleIcon from '@mui/icons-material/AccountCircle';
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
import { FolderPicker } from './FolderPicker';
import {
  useCreateSource,
  useDeleteSource,
  useGetLatestRun,
  useListSources,
  useSetSourceCredential,
  useStartIngestion,
  useUpdateSource,
} from '@/api/generated/sources/sources';
import { useStoreCredential } from '@/api/generated/credentials/credentials';
import type { SourceIn, SourceOut } from '@/api/generated/model';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

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
  if (!run) {
    return (
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
        Never run
      </Typography>
    );
  }

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

type DialogMode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; sourceId: string };

export const SourcesPanel = () => {
  const { data: sources = [], refetch } = useListSources();
  const create = useCreateSource();
  const update = useUpdateSource();
  const remove = useDeleteSource();
  const ingest = useStartIngestion();
  const setCredential = useSetSourceCredential();
  const storeCredential = useStoreCredential();

  const [searchParams, setSearchParams] = useSearchParams();
  const [dialog, setDialog] = useState<DialogMode>({ kind: 'closed' });
  const [draft, setDraft] = useState<SourceIn>(EMPTY);
  const [pendingSecret, setPendingSecret] = useState('');
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [folderIdInput, setFolderIdInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Google redirects the whole browser back here once OAuth completes (or
  // fails); there is no fetch() call for the frontend to await, only these
  // query params to read on the next render. Handled once per navigation and
  // then stripped, so a page refresh does not replay a stale connection.
  useEffect(() => {
    const status = searchParams.get('oauth_status');
    if (!status) return;

    if (status === 'error') {
      setError(searchParams.get('message') ?? 'Google Drive connection failed');
    } else if (status === 'connected') {
      const principal = searchParams.get('principal') ?? '';
      const credentialRef = searchParams.get('credential_ref') ?? '';
      const editingSourceId = searchParams.get('editing_source_id');
      setNotice(`Connected as ${principal}`);
      setDraft((current) => ({
        ...current,
        auth_mode: 'oauth',
        principal,
        credential_ref: credentialRef,
      }));
      setDialog(editingSourceId ? { kind: 'edit', sourceId: editingSourceId } : { kind: 'create' });
    }
    setSearchParams((params) => {
      params.delete('oauth_status');
      params.delete('message');
      params.delete('principal');
      params.delete('credential_ref');
      params.delete('editing_source_id');
      return params;
    }, { replace: true });
    // Only ever fires from a fresh set of URL params landing on mount/redirect;
    // searchParams itself is intentionally excluded to avoid re-running this
    // as a side effect of the setSearchParams call above.
  }, []);

  const openCreate = () => {
    setError(null);
    setDraft(EMPTY);
    setPendingSecret('');
    setDialog({ kind: 'create' });
  };

  const openEdit = (source: SourceOut) => {
    setError(null);
    setDraft({
      name: source.name,
      provider: source.provider ?? 'google_drive',
      auth_mode: source.auth_mode,
      principal: source.principal,
      credential_ref: source.credential_ref,
      root_folder_ids: source.root_folder_ids ?? [],
      include_mime_types: source.include_mime_types ?? [],
      exclude_mime_types: source.exclude_mime_types ?? [],
      max_document_bytes: source.max_document_bytes ?? null,
      enabled: source.enabled ?? true,
    });
    setPendingSecret('');
    setDialog({ kind: 'edit', sourceId: source.source_id });
  };

  const closeDialog = () => {
    setDialog({ kind: 'closed' });
    setDraft(EMPTY);
    setPendingSecret('');
  };

  const connectGoogleDrive = () => {
    const returnPath = '/configuration';
    const editingParam = dialog.kind === 'edit' ? `&editing_source_id=${dialog.sourceId}` : '';
    window.location.href =
      `${API_BASE_URL}/oauth/google/start?return_path=${encodeURIComponent(returnPath)}${editingParam}`;
  };

  const storeServiceAccountKey = async () => {
    if (!pendingSecret.trim()) return;
    setError(null);
    try {
      if (dialog.kind === 'edit') {
        // Rotates the credential this source already points at, rather than
        // provisioning a new one -- the source keeps the same credential_ref.
        await setCredential.mutateAsync({
          sourceId: dialog.sourceId,
          data: { secret: pendingSecret },
        });
        setNotice('Credential updated');
      } else {
        const stored = await storeCredential.mutateAsync({ data: { secret: pendingSecret } });
        setDraft((current) => ({ ...current, credential_ref: stored.credential_ref }));
        setNotice('Credential stored');
      }
      setPendingSecret('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not store the credential');
    }
  };

  const addFolderId = () => {
    const id = folderIdInput.trim();
    const current = draft.root_folder_ids ?? [];
    if (!id || current.includes(id)) return;
    setDraft({ ...draft, root_folder_ids: [...current, id] });
    setFolderIdInput('');
  };

  const removeFolderId = (id: string) => {
    setDraft({ ...draft, root_folder_ids: (draft.root_folder_ids ?? []).filter((f) => f !== id) });
  };

  const save = async () => {
    setError(null);
    try {
      if (dialog.kind === 'edit') {
        await update.mutateAsync({ sourceId: dialog.sourceId, data: draft });
      } else {
        await create.mutateAsync({ data: draft });
      }
      closeDialog();
      await refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not save the source');
    }
  };

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h6">Document sources</Typography>
        <Button startIcon={<AddIcon />} onClick={openCreate}>
          Add source
        </Button>
      </Stack>

      {notice && (
        <Alert severity="success" onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}

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
              <Typography variant="caption" color="text.secondary" display="block">
                {source.provider ?? 'google_drive'} · {source.auth_mode.replace(/_/g, ' ')} · as{' '}
                {source.principal}
                {(source.root_folder_ids?.length ?? 0) > 0 &&
                  ` · ${source.root_folder_ids?.length} root folder${source.root_folder_ids?.length === 1 ? '' : 's'}`}
              </Typography>
              <RunSummary sourceId={source.source_id} />
            </Box>
            <Stack direction="row">
              <IconButton
                aria-label={`Ingest ${source.name}`}
                onClick={() => ingest.mutate({ sourceId: source.source_id, params: {} })}
              >
                <PlayArrowIcon />
              </IconButton>
              <IconButton aria-label={`Edit ${source.name}`} onClick={() => openEdit(source)}>
                <EditIcon />
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

      <Dialog open={dialog.kind !== 'closed'} onClose={closeDialog} fullWidth maxWidth="sm">
        <DialogTitle>
          {dialog.kind === 'edit' ? 'Edit document source' : 'Add a document source'}
        </DialogTitle>
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
                  credential_ref: '',
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

            {draft.auth_mode === 'oauth' ? (
              <Stack spacing={1}>
                <Button
                  variant="outlined"
                  startIcon={<GoogleIcon />}
                  onClick={connectGoogleDrive}
                >
                  Connect Google Drive
                </Button>
                <TextField
                  label="Principal"
                  value={draft.principal}
                  helperText={
                    draft.credential_ref
                      ? 'Filled in automatically from the connected Google account.'
                      : 'Connect a Google account above to fill this in automatically.'
                  }
                  slotProps={{ input: { readOnly: true } }}
                  fullWidth
                />
              </Stack>
            ) : (
              <Stack spacing={1}>
                <TextField
                  label="Principal"
                  value={draft.principal}
                  onChange={(e) => setDraft({ ...draft, principal: e.target.value })}
                  helperText="The Workspace user to impersonate via domain-wide delegation."
                  fullWidth
                />
                <TextField
                  label="Service-account key"
                  value={pendingSecret}
                  onChange={(e) => setPendingSecret(e.target.value)}
                  multiline
                  minRows={3}
                  type="password"
                  helperText="Paste the JSON key, then store it. Stored encrypted; never readable back."
                  fullWidth
                />
                <Button
                  size="small"
                  disabled={!pendingSecret.trim()}
                  onClick={storeServiceAccountKey}
                >
                  {draft.credential_ref ? 'Replace stored key' : 'Store key'}
                </Button>
              </Stack>
            )}

            <Box>
              <Typography variant="caption" color="text.secondary" gutterBottom display="block">
                Root folders (empty = the whole Drive)
              </Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
                {(draft.root_folder_ids ?? []).map((id) => (
                  <Chip key={id} label={id} size="small" onDelete={() => removeFolderId(id)} />
                ))}
              </Stack>
              <Stack direction="row" spacing={1}>
                <Button
                  size="small"
                  startIcon={<FolderOpenIcon />}
                  disabled={!draft.credential_ref}
                  onClick={() => setFolderPickerOpen(true)}
                >
                  Browse
                </Button>
                <TextField
                  size="small"
                  placeholder="or paste a folder ID"
                  value={folderIdInput}
                  onChange={(e) => setFolderIdInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addFolderId();
                    }
                  }}
                  fullWidth
                />
              </Stack>
            </Box>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog}>Cancel</Button>
          <Button
            variant="contained"
            onClick={save}
            disabled={!draft.name || !draft.principal || !draft.credential_ref}
          >
            {dialog.kind === 'edit' ? 'Save changes' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      <FolderPicker
        open={folderPickerOpen}
        onClose={() => setFolderPickerOpen(false)}
        onPick={(folder) => {
          if (!(draft.root_folder_ids ?? []).includes(folder.id)) {
            setDraft((current) => ({
              ...current,
              root_folder_ids: [...(current.root_folder_ids ?? []), folder.id],
            }));
          }
        }}
        authMode={draft.auth_mode}
        principal={draft.principal}
        credentialRef={draft.credential_ref}
      />
    </Stack>
  );
};
