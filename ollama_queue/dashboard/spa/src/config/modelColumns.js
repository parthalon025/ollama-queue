// What it shows: Column definitions for the Models tab ShDataTable.
// Decision it drives: Which model fields are visible and in what order.
// Key names must match models.value row objects from stores/models.js.
export const MODEL_COLUMNS = [
    { key: 'name',               label: 'Model'         },
    { key: 'size_bytes',         label: 'Size'          },
    { key: 'parameter_size',     label: 'Parameters'    },
    { key: 'quantization_level', label: 'Quantization'  },
];
