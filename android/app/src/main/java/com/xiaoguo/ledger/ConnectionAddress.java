package com.xiaoguo.ledger;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** A ledger endpoint, with HTTP limited to private IPv4 LAN addresses. */
public final class ConnectionAddress {
    private final URI uri;

    private ConnectionAddress(URI uri) {
        this.uri = uri;
    }

    public static ConnectionAddress parse(String input) {
        if (input == null || input.trim().isEmpty()) {
            throw new IllegalArgumentException("请输入电脑软件显示的手机地址");
        }
        URI parsed;
        try {
            parsed = new URI(input.trim());
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("地址格式不正确，请从电脑软件复制完整地址");
        }
        String scheme = parsed.getScheme() == null ? "" : parsed.getScheme().toLowerCase(Locale.ROOT);
        String host = parsed.getHost();
        if (!(scheme.equals("http") || scheme.equals("https")) || host == null
                || parsed.getRawUserInfo() != null || parsed.getRawQuery() != null
                || parsed.getRawFragment() != null || parsed.getPort() > 65535
                || (parsed.getRawPath() != null && !parsed.getRawPath().isEmpty()
                && !parsed.getRawPath().equals("/"))) {
            throw new IllegalArgumentException("请填写完整账本地址，不要添加路径、账号或参数");
        }
        if (scheme.equals("http") && !isPrivateIpv4(host)) {
            throw new IllegalArgumentException("HTTP 只支持店内局域网 IP；外网地址请使用 HTTPS");
        }
        try {
            return new ConnectionAddress(new URI(scheme, null,
                    host.toLowerCase(Locale.ROOT), parsed.getPort(), null, null, null));
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("地址格式不正确，请从电脑软件复制完整地址");
        }
    }

    public String baseUrl() {
        return uri.toASCIIString();
    }

    public String hostLabel() {
        return uri.getHost();
    }

    public boolean sameOrigin(String candidate) {
        try {
            URI other = new URI(candidate);
            return uri.getScheme().equalsIgnoreCase(other.getScheme())
                    && uri.getHost().equalsIgnoreCase(other.getHost())
                    && effectivePort(uri) == effectivePort(other);
        } catch (URISyntaxException | NullPointerException error) {
            return false;
        }
    }

    private static int effectivePort(URI value) {
        if (value.getPort() >= 0) return value.getPort();
        return "https".equalsIgnoreCase(value.getScheme()) ? 443 : 80;
    }

    private static boolean isPrivateIpv4(String host) {
        String[] octets = host.split("\\.", -1);
        if (octets.length != 4) return false;
        int[] values = new int[4];
        for (int i = 0; i < 4; i++) {
            if (octets[i].isEmpty() || octets[i].length() > 3
                    || (octets[i].length() > 1 && octets[i].startsWith("0"))) return false;
            for (int j = 0; j < octets[i].length(); j++) {
                if (!Character.isDigit(octets[i].charAt(j))) return false;
            }
            try {
                values[i] = Integer.parseInt(octets[i]);
            } catch (NumberFormatException error) {
                return false;
            }
            if (values[i] > 255) return false;
        }
        return values[0] == 10
                || (values[0] == 172 && values[1] >= 16 && values[1] <= 31)
                || (values[0] == 192 && values[1] == 168);
    }
}
