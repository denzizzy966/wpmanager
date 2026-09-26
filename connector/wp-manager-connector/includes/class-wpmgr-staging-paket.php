<?php
if ( ! defined( 'ABSPATH' ) && ! defined( 'WPMGR_TESTING' ) ) {
    define( 'WPMGR_TESTING', true );
}

/**
 * Paket biner staging: beberapa bagian (isi berkas, rentang berkas besar,
 * atau SQL) dalam satu body, masing-masing dengan sha256 sendiri (spec §6.2
 * "hash per potongan"). Format yang sama dipakai dua arah -- balasan
 * /staging/file dan /staging/tabel, serta body /staging/unggah -- dan
 * diurai dengan aturan yang sama oleh wpmgr.staging.paket di dashboard.
 *
 *   "WPMGRPAK1\n" + 8 hex panjang meta + "\n" + meta JSON + bagian...
 */
class WPMGR_Staging_Paket {

    const MAGIC     = "WPMGRPAK1\n";
    // Fix R1 minor (b): diturunkan dari 4 MiB ke 1 MiB -- metadata paket
    // hanya berisi daftar path/ukuran/sha256, tidak pernah sebesar itu dalam
    // pemakaian sah; batas kecil mengurangi permukaan serangan alokasi
    // memori dari input yang belum diverifikasi.
    const MAKS_META = 1048576;

    private static function rusak( $pesan ) {
        return new WP_Error( 'wpmgr_staging_paket', $pesan, array( 'status' => 400 ) );
    }

    /**
     * Fix R1 minor (a): meta['berkas'] wajib berkunci 0..n-1 tanpa lubang
     * maupun urutan acak. urai() mencocokkan setiap entri meta ke bagian
     * isi berikutnya berdasarkan URUTAN ITERASI array, bukan nilai kuncinya;
     * kunci yang berlubang atau tidak berurutan (mis. hasil JSON objek
     * `{"0":...,"2":...}` yang direkayasa tangan) membuat pencocokan itu
     * tidak lagi bisa dipercaya mewakili urutan bagian yang sebenarnya.
     */
    private static function kunci_berurutan( array $x ) {
        return array_keys( $x ) === ( empty( $x ) ? array() : range( 0, count( $x ) - 1 ) );
    }

    public static function susun( array $meta, array $isi ) {
        $isi    = array_values( $isi );
        $berkas = ( isset( $meta['berkas'] ) && is_array( $meta['berkas'] ) ) ? array_values( $meta['berkas'] ) : array();
        if ( count( $berkas ) !== count( $isi ) ) {
            throw new InvalidArgumentException( 'Jumlah entri meta dan bagian paket tidak sama.' );
        }
        foreach ( $isi as $i => $data ) {
            $berkas[ $i ]['ukuran'] = strlen( $data );
            $berkas[ $i ]['sha256'] = hash( 'sha256', $data );
        }
        $meta['berkas'] = $berkas;
        $json = json_encode( $meta, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
        if ( false === $json || strlen( $json ) > self::MAKS_META ) {
            throw new InvalidArgumentException( 'Meta paket tidak dapat dikodekan.' );
        }
        return self::MAGIC . sprintf( '%08x', strlen( $json ) ) . "\n" . $json . implode( '', $isi );
    }

    public static function urai( $data ) {
        $data  = (string) $data;
        $awal  = strlen( self::MAGIC );
        $total = strlen( $data );
        if ( $total < $awal + 9 || 0 !== strncmp( $data, self::MAGIC, $awal ) ) {
            return self::rusak( 'Bukan paket staging.' );
        }
        $hex = substr( $data, $awal, 8 );
        if ( 1 !== preg_match( '/^[0-9a-f]{8}\z/', $hex ) || "\n" !== $data[ $awal + 8 ] ) {
            return self::rusak( 'Kepala paket rusak.' );
        }
        $panjang = hexdec( $hex );
        if ( $panjang > self::MAKS_META || $awal + 9 + $panjang > $total ) {
            return self::rusak( 'Panjang meta paket tidak sah.' );
        }
        // Depth 8 (fix R1 minor b): meta paket selalu dangkal (objek -> daftar
        // berkas -> field skalar); membatasi depth mencegah json_decode()
        // dipaksa mengurai struktur bersarang dalam-dalam dari input yang
        // belum dipercaya.
        $meta = json_decode( substr( $data, $awal + 9, $panjang ), true, 8 );
        if ( ! is_array( $meta ) || ! isset( $meta['berkas'] ) || ! is_array( $meta['berkas'] )
            || ! self::kunci_berurutan( $meta['berkas'] ) ) {
            return self::rusak( 'Meta paket tidak sah.' );
        }
        $posisi = $awal + 9 + $panjang;
        $bagian = array();
        foreach ( $meta['berkas'] as $b ) {
            if ( ! is_array( $b ) || ! isset( $b['ukuran'], $b['sha256'] ) || ! is_int( $b['ukuran'] )
                || $b['ukuran'] < 0 || ! is_string( $b['sha256'] ) ) {
                return self::rusak( 'Entri paket tidak sah.' );
            }
            if ( $posisi + $b['ukuran'] > $total ) {
                return self::rusak( 'Paket terpotong.' );
            }
            $isi = 0 === $b['ukuran'] ? '' : (string) substr( $data, $posisi, $b['ukuran'] );
            if ( ! hash_equals( strtolower( $b['sha256'] ), hash( 'sha256', $isi ) ) ) {
                return new WP_Error( 'wpmgr_staging_hash', 'Hash potongan tidak cocok.', array( 'status' => 422 ) );
            }
            $bagian[] = $isi;
            $posisi  += $b['ukuran'];
        }
        if ( $posisi !== $total ) {
            return self::rusak( 'Ada data sisa di akhir paket.' );
        }
        return array( $meta, $bagian );
    }
}
