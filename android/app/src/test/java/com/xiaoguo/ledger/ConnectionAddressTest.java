package com.xiaoguo.ledger;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class ConnectionAddressTest {
    @Test public void acceptsPrivateLanAndSameOrigin() {
        ConnectionAddress address = ConnectionAddress.parse(" http://192.168.0.113:8765/ ");
        assertEquals("http://192.168.0.113:8765", address.baseUrl());
        assertTrue(address.sameOrigin("http://192.168.0.113:8765/api/status"));
        assertFalse(address.sameOrigin("http://192.168.0.113:8766/api/status"));
        assertFalse(address.sameOrigin("http://192.168.0.114:8765/api/status"));
    }

    @Test public void permitsHttpsForExternalAddress() {
        assertEquals("https://ledger.example.com", ConnectionAddress.parse(
                "https://ledger.example.com/").baseUrl());
    }

    @Test public void rejectsPublicHttpAndEmbeddedCredentials() {
        assertRejected("http://example.com:8765");
        assertRejected("http://8.8.8.8:8765");
        assertRejected("http://127.0.0.1:8765");
        assertRejected("http://192.168.001.113:8765");
        assertRejected("http://user:pass@192.168.0.113:8765");
        assertRejected("http://192.168.0.113:8765/other");
        assertRejected("javascript:alert(1)");
    }

    private void assertRejected(String value) {
        try {
            ConnectionAddress.parse(value);
            throw new AssertionError("Accepted " + value);
        } catch (IllegalArgumentException expected) {
            // Invalid addresses must never be loaded in WebView.
        }
    }
}
