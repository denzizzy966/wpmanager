const WARNA_STATUS = {
  active: 'dg-badge-success',
  pending_pair: 'dg-badge-info',
  needs_reconnect: 'dg-badge-warning',
  blocked: 'dg-badge-danger',
  unreachable: 'dg-badge-danger',
  disabled: 'dg-badge-muted',
};

function layarSite() {
  return {
    grid: null,

    async muat() {
      const data = await (await fetch('/api/sites')).json();
      if (this.grid) {
        // setData() menggambar ulang grid sendiri (lihat updates.js); tidak
        // perlu memanggil refresh() sesudahnya.
        this.grid.setData(data);
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: data,
        keyExpr: 'id',
        columns: [
          { dataField: 'nama', caption: 'Site' },
          { dataField: 'client_nama', caption: 'Client' },
          {
            dataField: 'status',
            caption: 'Status',
            cellTemplate: (v) => `<span class="${WARNA_STATUS[v] || ''}">${v}</span>`,
          },
          { dataField: 'wp_version', caption: 'WP' },
          { dataField: 'php_version', caption: 'PHP' },
          { dataField: 'jumlah_update', caption: 'Update' },
          { dataField: 'last_seen_at', caption: 'Terakhir Terlihat', dataType: 'date' },
          {
            caption: 'Aksi',
            calculateCellValue: (baris) => baris.id,
            cellTemplate: (id) =>
              `<button data-sso="${id}">Masuk</button> ` +
              `<button data-scan="${id}">Scan</button> ` +
              `<a href="/sites/${id}">Detail</a>`,
          },
        ],
      });

      // Delegasi event pada wadah grid, bukan pada tiap tombol: DataGrid
      // menggambar ulang isinya setiap kali disortir, difilter, dikelompokkan,
      // atau dipaginasi, sehingga listener yang menempel langsung pada tombol
      // akan lenyap begitu tombol itu digambar ulang. Listener pada wadah
      // tetap hidup melewati setiap penggambaran ulang.
      document.getElementById('grid').addEventListener('click', async (ev) => {
        const sso = ev.target.getAttribute('data-sso');
        const scan = ev.target.getAttribute('data-scan');
        if (sso) {
          const r = await (await fetch(`/api/sso/${sso}`)).json();
          window.open(r.url, '_blank', 'noopener');
        } else if (scan) {
          await fetch('/api/jobs/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ site_id: scan }),
          });
          ev.target.textContent = 'Antre…';
        }
      });
    },
  };
}
