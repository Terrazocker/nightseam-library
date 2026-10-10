/* global application state */

export const S = {
  hostCapabilities: { native_folder_picker: false },
  cfg: null,          // /api/state
  library: null,      // /api/library
  disabledTypes: new Set(),   // object types greyed out on the sky map legend
};
