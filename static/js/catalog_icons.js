const paths = {
  folder: '<path d="M2.75 20.25V6.4c0-.95.77-1.72 1.72-1.72h5.85l2.77 3h6.44c.95 0 1.72.77 1.72 1.72v10.85c0 .95-.77 1.72-1.72 1.72H4.47c-.95 0-1.72-.77-1.72-1.72Z"/>',
  file: '<path d="M5.25 2h8.1L21 9.75v15.5H5.25a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M13.25 2v7.75H21"/>',
  database: '<ellipse cx="20" cy="7.5" rx="15.5" ry="5.25"/><path d="M4.5 7.5v25c0 2.9 6.94 5.25 15.5 5.25s15.5-2.35 15.5-5.25v-25M4.5 16c0 2.9 6.94 5.25 15.5 5.25S35.5 18.9 35.5 16M4.5 24.25c0 2.9 6.94 5.25 15.5 5.25s15.5-2.35 15.5-5.25"/>',
  clock: '<circle cx="20" cy="20" r="17"/><path d="M20 9v11l8 5"/>',
  search: '<circle cx="10.7" cy="10.7" r="6.3"/><path d="m15.5 15.5 5 5"/>',
  check: '<path d="m4.5 12.5 5 5L20 6.75"/>',
  warning: '<path d="M20 3.5 37 34H3L20 3.5Z"/><path d="M20 14v9m0 5v.1"/>'
};

export function catalogIcon(name, className = "") {
  const body = paths[name];
  if (!body) return "";
  const extra = className ? ` ${className}` : "";
  const viewBox = name === "database" || name === "clock" ? "0 0 40 40"
    : name === "warning" ? "0 0 40 40" : "0 0 24 28";
  return `<svg class="catalog-symbol${extra}" viewBox="${viewBox}" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}
