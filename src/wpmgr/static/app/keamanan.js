function layarKeamanan() {
  return {
    grid: null,
    jam: 24,
    galat: '',

    async muat() {
      this.galat = '';
      try {
        const r = await fetch(`/api/keamanan/penyerang?jam=${encodeURIComponent(this.jam)}`);
        if (!r.ok) {
          this.galat = `Data tidak dapat dimuat. ${await pesanGalat(r)}`;
          return;
        }
        const data = await r.json();
        if (this.grid) {
          this.grid.setData(data);
          return;
        }
        // Tanpa cellTemplate: DataGrid meng-escape sendiri isi sel biasa, dan
        // semua kolom di sini berasal dari site klien (username, UA).
        this.grid = new DataGrid('#grid', {
          dataSource: data,
          keyExpr: 'ip',
          selection: false,
          columns: [
            { dataField: 'ip', caption: 'IP' },
            { dataField: 'negara', caption: 'Negara' },
            { dataField: 'jumlah', caption: 'Percobaan' },
            { dataField: 'jumlah_site', caption: 'Site diserang' },
            { dataField: 'site', caption: 'Site' },
            { dataField: 'username', caption: 'Username dicoba' },
            { dataField: 'jalur', caption: 'Jalur' },
            { caption: 'Alat', calculateCellValue: (b) => (b.skrip ? 'skrip' : '') },
            { dataField: 'terakhir', caption: 'Terakhir', dataType: 'date' },
          ],
        });
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
      }
    },
  };
}
