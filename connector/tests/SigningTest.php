<?php
use PHPUnit\Framework\TestCase;

final class SigningTest extends TestCase {

    private function vectors(): array {
        $path = __DIR__ . '/../../tests/fixtures/hmac-test-vectors.json';
        $this->assertFileExists( $path, 'Jalankan python scripts/gen_hmac_vectors.py lebih dulu' );
        return json_decode( file_get_contents( $path ), true );
    }

    public function test_canonical_cocok_dengan_fixture(): void {
        foreach ( $this->vectors() as $v ) {
            $this->assertSame(
                $v['canonical'],
                WPMGR_Signing::canonical( $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] ),
                "canonical meleset pada kasus {$v['nama']}"
            );
        }
    }

    public function test_signature_cocok_dengan_fixture(): void {
        foreach ( $this->vectors() as $v ) {
            $this->assertSame(
                $v['signature'],
                WPMGR_Signing::sign( $v['secret_hex'], $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] ),
                "signature meleset pada kasus {$v['nama']}"
            );
        }
    }

    public function test_verify_menerima_yang_benar(): void {
        $v = $this->vectors()[0];
        $this->assertTrue(
            WPMGR_Signing::verify( $v['secret_hex'], $v['signature'], $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] )
        );
    }

    public function test_verify_menolak_yang_salah(): void {
        $v = $this->vectors()[0];
        $this->assertFalse(
            WPMGR_Signing::verify( $v['secret_hex'], str_repeat( '0', 64 ), $v['method'], $v['path'], $v['timestamp'], $v['nonce'], $v['body'] )
        );
    }

    public function test_method_dibesarkan(): void {
        $this->assertStringStartsWith( 'GET' . "\n", WPMGR_Signing::canonical( 'get', '/x', 1, 'n', '' ) );
    }

    public function test_canonical_punya_empat_newline(): void {
        $this->assertSame( 4, substr_count( WPMGR_Signing::canonical( 'POST', '/x', 1, 'n', '{}' ), "\n" ) );
    }
}
