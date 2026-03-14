// What it shows: Column definitions for the History tab ShDataTable.
// Decision it drives: Which job fields are visible and in what order.
// Key names must match history.value row objects from stores/queue.js.
export const HISTORY_COLUMNS = [
    { key: 'id',           label: 'ID'       },
    { key: 'source',       label: 'Source'   },
    { key: 'model',        label: 'Model'    },
    { key: 'status',       label: 'Status'   },
    { key: 'duration_s',   label: 'Duration' },
    { key: 'submitted_at', label: 'Submitted'},
];
