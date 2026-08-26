/**
 * Evaluation management (ADR-0010).
 *
 * Two counts get prominence over the averages: cases the retriever missed
 * entirely, and answers judged fluent-but-ungrounded. Each points at a specific
 * remedy — the first at ingestion or chunking, the second at the prompt — where
 * a mean score points at nothing.
 *
 * Cases are viewed and edited per dataset, expanded on demand: `listDatasets`
 * deliberately omits case bodies (a listing reporting the right count but no
 * content is cheap to fetch), so the full list is only pulled once a dataset is
 * expanded. Editing forks the dataset version rather than mutating in place
 * (ADR-0010) -- the API mirrors that with PUT/DELETE per case, so the UI does
 * too rather than pretending a case can change without the version moving.
 */

import { useState } from 'react';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditIcon from '@mui/icons-material/Edit';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useQueryClient } from '@tanstack/react-query';
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
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import {
  getGetDatasetQueryKey,
  getListDatasetsQueryKey,
  useAddCase,
  useCreateDataset,
  useGetDataset,
  useListDatasets,
  useListEvaluationRuns,
  useRemoveCase,
  useStartEvaluation,
  useUpdateCase,
} from '@/api/generated/evaluation/evaluation';
import type { CaseOut } from '@/api/generated/model';

const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(0)}%`;

const chipSx = { height: 20, fontSize: 11 } as const;

const RunHistory = ({ datasetId }: { datasetId: string }) => {
  const { data: runs = [] } = useListEvaluationRuns(
    datasetId,
    {},
    // Runs take minutes; polling is how progress shows without a second
    // transport existing only for this panel.
    { query: { refetchInterval: 4000 } },
  );
  if (runs.length === 0) return null;

  return (
    <Stack spacing={1} sx={{ mt: 1.5 }}>
      {runs.slice(0, 4).map((run) => (
        <Paper key={run.run_id} variant="outlined" sx={{ p: 1 }}>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap alignItems="center">
            <Chip size="small" label={run.state} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`v${run.dataset_version}`} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`recall ${percent(run.mean_recall)}`} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`nDCG ${percent(run.mean_ndcg)}`} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`faithful ${percent(run.mean_faithfulness)}`} sx={chipSx} />
            {run.missed_entirely_count > 0 && (
              <Chip
                size="small"
                color="warning"
                label={`${run.missed_entirely_count} never retrieved`}
                sx={chipSx}
              />
            )}
            {run.hallucination_count > 0 && (
              <Chip
                size="small"
                color="error"
                label={`${run.hallucination_count} ungrounded`}
                sx={chipSx}
              />
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {run.config.embedding_model} · {run.config.chat_model} · rrf k=
            {run.config.rrf_k} · rerank {run.config.rerank_enabled ? 'on' : 'off'}
          </Typography>
        </Paper>
      ))}
    </Stack>
  );
};

const DatasetCases = ({
  datasetId,
  onEdit,
}: {
  datasetId: string;
  onEdit: (kase: CaseOut) => void;
}) => {
  const { data: dataset, isPending } = useGetDataset(datasetId);
  const remove = useRemoveCase();
  const queryClient = useQueryClient();

  const removeCase = async (caseId: string) => {
    await remove.mutateAsync({ datasetId, caseId });
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: getGetDatasetQueryKey(datasetId) }),
      queryClient.invalidateQueries({ queryKey: getListDatasetsQueryKey() }),
    ]);
  };

  if (isPending) {
    return (
      <Typography variant="caption" color="text.secondary">
        Loading cases…
      </Typography>
    );
  }

  const cases = dataset?.cases ?? [];
  if (cases.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary">
        No cases yet.
      </Typography>
    );
  }

  return (
    <Stack spacing={1}>
      {cases.map((kase) => (
        <Paper key={kase.case_id} variant="outlined" sx={{ p: 1 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2">{kase.question}</Typography>
              {kase.expected_answer && (
                <Typography variant="caption" color="text.secondary" display="block">
                  Expected: {kase.expected_answer}
                </Typography>
              )}
              <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
                {kase.scores_retrieval && <Chip size="small" label="retrieval" sx={chipSx} />}
                {kase.scores_generation && <Chip size="small" label="generation" sx={chipSx} />}
              </Stack>
            </Box>
            <Stack direction="row" sx={{ flexShrink: 0 }}>
              <IconButton
                size="small"
                aria-label={`Edit case: ${kase.question}`}
                onClick={() => onEdit(kase)}
              >
                <EditIcon fontSize="small" />
              </IconButton>
              <IconButton
                size="small"
                aria-label={`Delete case: ${kase.question}`}
                onClick={() => removeCase(kase.case_id)}
              >
                <DeleteOutlineIcon fontSize="small" />
              </IconButton>
            </Stack>
          </Stack>
        </Paper>
      ))}
    </Stack>
  );
};

type CaseDialogMode =
  | { kind: 'closed' }
  | { kind: 'add'; datasetId: string }
  | { kind: 'edit'; datasetId: string; original: CaseOut };

export const EvalsPanel = () => {
  const { data: datasets = [], refetch } = useListDatasets();
  const createDataset = useCreateDataset();
  const addCase = useAddCase();
  const updateCase = useUpdateCase();
  const startRun = useStartEvaluation();
  const queryClient = useQueryClient();

  const [newName, setNewName] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [caseDialog, setCaseDialog] = useState<CaseDialogMode>({ kind: 'closed' });
  const [question, setQuestion] = useState('');
  const [expected, setExpected] = useState('');
  const [error, setError] = useState<string | null>(null);

  const toggleExpanded = (datasetId: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(datasetId)) next.delete(datasetId);
      else next.add(datasetId);
      return next;
    });
  };

  const openAddCase = (datasetId: string) => {
    setError(null);
    setQuestion('');
    setExpected('');
    setCaseDialog({ kind: 'add', datasetId });
  };

  const openEditCase = (datasetId: string, kase: CaseOut) => {
    setError(null);
    setQuestion(kase.question);
    setExpected(kase.expected_answer ?? '');
    setCaseDialog({ kind: 'edit', datasetId, original: kase });
  };

  const closeCaseDialog = () => {
    setCaseDialog({ kind: 'closed' });
    setQuestion('');
    setExpected('');
  };

  const saveCase = async () => {
    if (caseDialog.kind === 'closed') return;
    setError(null);
    const datasetId = caseDialog.datasetId;
    try {
      if (caseDialog.kind === 'add') {
        await addCase.mutateAsync({
          datasetId,
          data: {
            question,
            expected_answer: expected.trim() || null,
            expected_chunk_ids: [],
            tags: [],
            source_turn_id: null,
            notes: null,
          },
        });
      } else {
        const { original } = caseDialog;
        await updateCase.mutateAsync({
          datasetId,
          caseId: original.case_id,
          data: {
            question,
            expected_answer: expected.trim() || null,
            expected_chunk_ids: original.expected_chunk_ids ?? [],
            tags: original.tags ?? [],
            source_turn_id: original.source_turn_id ?? null,
            notes: original.notes ?? null,
          },
        });
      }
      closeCaseDialog();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: getListDatasetsQueryKey() }),
        queryClient.invalidateQueries({ queryKey: getGetDatasetQueryKey(datasetId) }),
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not save the case');
    }
  };

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Evaluation</Typography>
      {error && <Alert severity="error">{error}</Alert>}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1}>
          <TextField
            size="small"
            fullWidth
            label="New dataset name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <Button
            startIcon={<AddIcon />}
            disabled={!newName.trim()}
            onClick={async () => {
              await createDataset.mutateAsync({ data: { name: newName } });
              setNewName('');
              await refetch();
            }}
          >
            Create
          </Button>
        </Stack>
      </Paper>

      {datasets.length === 0 && (
        <Alert severity="info">
          No datasets yet. Create one, then add cases — ideally by promoting real
          answers that were wrong, which keeps the set grounded in actual failures.
        </Alert>
      )}

      {datasets.map((dataset) => {
        const isExpanded = expanded.has(dataset.dataset_id);
        return (
          <Paper key={dataset.dataset_id} variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Stack>
                <Typography variant="subtitle2">{dataset.name}</Typography>
                <Typography variant="caption" color="text.secondary">
                  version {dataset.version} · {dataset.case_count} case
                  {dataset.case_count === 1 ? '' : 's'}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1}>
                <Button
                  size="small"
                  endIcon={isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                  onClick={() => toggleExpanded(dataset.dataset_id)}
                >
                  Cases
                </Button>
                <Button size="small" onClick={() => openAddCase(dataset.dataset_id)}>
                  Add case
                </Button>
                <Button
                  size="small"
                  startIcon={<PlayArrowIcon />}
                  variant="contained"
                  disabled={dataset.case_count === 0}
                  onClick={async () => {
                    setError(null);
                    try {
                      await startRun.mutateAsync({ datasetId: dataset.dataset_id });
                    } catch (e) {
                      setError(e instanceof Error ? e.message : 'run failed to start');
                    }
                  }}
                >
                  Run
                </Button>
              </Stack>
            </Stack>

            {isExpanded && (
              <Box sx={{ mt: 1.5 }}>
                <DatasetCases
                  datasetId={dataset.dataset_id}
                  onEdit={(kase) => openEditCase(dataset.dataset_id, kase)}
                />
              </Box>
            )}

            <RunHistory datasetId={dataset.dataset_id} />
          </Paper>
        );
      })}

      <Dialog open={caseDialog.kind !== 'closed'} onClose={closeCaseDialog} fullWidth>
        <DialogTitle>
          {caseDialog.kind === 'edit' ? 'Edit evaluation case' : 'Add an evaluation case'}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label="Question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              fullWidth
              multiline
            />
            <TextField
              label="Expected answer (optional)"
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
              helperText="Supplying one enables generation scoring. Leave blank to score retrieval only."
              fullWidth
              multiline
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeCaseDialog}>Cancel</Button>
          <Button variant="contained" disabled={!question.trim()} onClick={saveCase}>
            {caseDialog.kind === 'edit' ? 'Save changes' : 'Add'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};
