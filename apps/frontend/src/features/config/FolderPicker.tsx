/**
 * Browse a Google Drive and pick folders to ingest (ADR-0016).
 *
 * Works from a `credential_ref` alone, before any source row exists -- the
 * whole point of decoupling credential storage from source creation was to
 * make this possible from the create dialog rather than only after saving.
 *
 * Deliberately a plain breadcrumb + list, not a tree view: a source's
 * ingestion root is usually one or two folders down from My Drive, and a full
 * expandable tree is more control than that decision needs. Shared Drives are
 * a known gap here (see GoogleDriveSource.list_folders); the "paste an ID"
 * fallback in SourcesPanel covers that case.
 */

import { useEffect, useState } from 'react';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import FolderIcon from '@mui/icons-material/Folder';
import HomeIcon from '@mui/icons-material/Home';
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Skeleton,
  Stack,
} from '@mui/material';
import { useBrowseFolders } from '@/api/generated/sources/sources';

interface Crumb {
  id: string;
  name: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onPick: (folder: { id: string; name: string }) => void;
  authMode: 'service_account' | 'oauth';
  principal: string;
  credentialRef: string;
}

export const FolderPicker = ({
  open,
  onClose,
  onPick,
  authMode,
  principal,
  credentialRef,
}: Props) => {
  const [trail, setTrail] = useState<Crumb[]>([{ id: 'root', name: 'My Drive' }]);
  const current = trail[trail.length - 1];

  // browseFolders is a POST (it carries the credential in the body rather
  // than the query string), so orval generates a mutation rather than a
  // query -- fired by hand whenever the visible folder changes.
  const { mutate, data, isPending, error } = useBrowseFolders();
  const parentId = current?.id ?? 'root';

  useEffect(() => {
    if (!open || !current) return;
    mutate({
      data: {
        auth_mode: authMode,
        principal,
        credential_ref: credentialRef,
        parent_id: parentId,
      },
    });
  }, [open, parentId, authMode, principal, credentialRef, mutate]);

  const reset = () => setTrail([{ id: 'root', name: 'My Drive' }]);

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle>Choose a folder</DialogTitle>
      <DialogContent>
        <Breadcrumbs separator={<ChevronRightIcon fontSize="small" />} sx={{ mb: 1 }}>
          {trail.map((crumb, index) => (
            <Link
              key={crumb.id}
              component="button"
              underline={index === trail.length - 1 ? 'none' : 'hover'}
              color={index === trail.length - 1 ? 'text.primary' : 'primary'}
              onClick={() => setTrail(trail.slice(0, index + 1))}
              sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}
            >
              {index === 0 && <HomeIcon fontSize="inherit" />}
              {crumb.name}
            </Link>
          ))}
        </Breadcrumbs>

        {error && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {error instanceof Error ? error.message : 'could not list folders'}
          </Alert>
        )}

        <Box sx={{ minHeight: 200, maxHeight: 320, overflowY: 'auto' }}>
          {isPending && (
            <Stack spacing={1}>
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} height={40} />
              ))}
            </Stack>
          )}

          {!isPending && data && data.folders.length === 0 && (
            <Alert severity="info">No subfolders here.</Alert>
          )}

          <List dense disablePadding>
            {data?.folders.map((folder) => (
              <ListItemButton
                key={folder.id}
                onClick={() => setTrail([...trail, { id: folder.id, name: folder.name }])}
              >
                <ListItemIcon sx={{ minWidth: 32 }}>
                  <FolderIcon fontSize="small" />
                </ListItemIcon>
                <ListItemText primary={folder.name} />
                <ChevronRightIcon fontSize="small" sx={{ opacity: 0.5 }} />
              </ListItemButton>
            ))}
          </List>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button
          onClick={() => {
            reset();
            onClose();
          }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={!current}
          onClick={() => {
            if (current) onPick(current);
            reset();
            onClose();
          }}
        >
          Use "{current?.name}"
        </Button>
      </DialogActions>
    </Dialog>
  );
};
