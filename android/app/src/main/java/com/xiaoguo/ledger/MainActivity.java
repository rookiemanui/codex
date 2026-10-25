package com.xiaoguo.ledger;

import android.app.Activity;
import android.annotation.SuppressLint;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.print.PrintManager;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.net.http.SslError;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedDispatcher;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Android companion for the existing computer-hosted ledger. */
public final class MainActivity extends Activity {
    private static final int PICK_UPLOAD = 1001;
    private static final int SAVE_DOWNLOAD = 1002;
    private static final int BRAND = Color.rgb(23, 78, 72);
    private static final int BACKGROUND = Color.rgb(245, 247, 244);
    private static final String PREFS = "connection";
    private static final String SAVED_URL = "computer_url";

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private int connectGeneration;
    private ConnectionAddress activeAddress;
    private WebView webView;
    private LinearLayout root;
    private ScrollView connectionScreen;
    private FrameLayout webScreen;
    private LinearLayout offlineOverlay;
    private ProgressBar progress;
    private EditText addressInput;
    private Button connectButton;
    private TextView errorText;
    private TextView hostText;
    private TextView changeAddress;
    private ValueCallback<Uri[]> uploadCallback;
    private String pendingDownloadUrl;
    private boolean navigationFailed;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        createInterface();
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        }
        String saved = getSharedPreferences(PREFS, MODE_PRIVATE).getString(SAVED_URL, "");
        if (!saved.isEmpty()) {
            addressInput.setText(saved);
            connect();
        }
    }

    private void createInterface() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BACKGROUND);
        if (Build.VERSION.SDK_INT >= 30) {
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
        } else {
            root.setFitsSystemWindows(true);
        }
        setContentView(root);
        createToolbar();
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        progress.setVisibility(View.GONE);
        root.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));

        FrameLayout body = new FrameLayout(this);
        root.addView(body, new LinearLayout.LayoutParams(-1, 0, 1));
        createConnectionScreen(body);
        createWebScreen(body);
        showConnection();
    }

    private void createToolbar() {
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(18), dp(12), dp(18), dp(12));
        toolbar.setBackgroundColor(Color.WHITE);
        root.addView(toolbar, new LinearLayout.LayoutParams(-1, dp(64)));

        TextView logo = label("锅", 22, Color.WHITE, Typeface.BOLD);
        logo.setGravity(Gravity.CENTER);
        logo.setBackground(rounded(BRAND, dp(11)));
        toolbar.addView(logo, new LinearLayout.LayoutParams(dp(40), dp(40)));

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(0, -2, 1);
        titleParams.leftMargin = dp(11);
        toolbar.addView(titles, titleParams);
        titles.addView(label("小锅账本", 17, Color.rgb(24, 47, 42), Typeface.BOLD));
        hostText = label("手机与电脑共用一本账", 11, Color.rgb(110, 124, 116), Typeface.NORMAL);
        titles.addView(hostText);

        changeAddress = label("更换地址", 12, BRAND, Typeface.BOLD);
        changeAddress.setPadding(dp(8), dp(12), 0, dp(12));
        changeAddress.setOnClickListener(view -> showConnection());
        toolbar.addView(changeAddress);
    }

    private void createConnectionScreen(FrameLayout parent) {
        connectionScreen = new ScrollView(this);
        connectionScreen.setFillViewport(true);
        parent.addView(connectionScreen, new FrameLayout.LayoutParams(-1, -1));
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(24), dp(36), dp(24), dp(28));
        connectionScreen.addView(content);

        TextView symbol = label("锅", 33, Color.WHITE, Typeface.BOLD);
        symbol.setGravity(Gravity.CENTER);
        symbol.setBackground(rounded(BRAND, dp(20)));
        content.addView(symbol, new LinearLayout.LayoutParams(dp(70), dp(70)));
        TextView title = label("连接小锅账本", 27, Color.rgb(20, 47, 40), Typeface.BOLD);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-1, -2);
        titleParams.topMargin = dp(25);
        content.addView(title, titleParams);
        TextView subtitle = label("手机和电脑实时使用同一本账", 15,
                Color.rgb(96, 113, 104), Typeface.NORMAL);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(-1, -2);
        subtitleParams.topMargin = dp(7);
        content.addView(subtitle, subtitleParams);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(19), dp(20), dp(19), dp(21));
        card.setBackground(rounded(Color.WHITE, dp(17)));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(-1, -2);
        cardParams.topMargin = dp(29);
        content.addView(card, cardParams);
        card.addView(label("连接前请确认", 14, Color.rgb(28, 63, 53), Typeface.BOLD));
        addInstruction(card, "1  电脑已打开小锅账本，服务正在运行");
        addInstruction(card, "2  手机与电脑连接同一个 Wi-Fi");
        addInstruction(card, "3  在电脑右上角“连接与退出”查看手机地址");

        TextView fieldLabel = label("电脑显示的手机地址", 13, Color.rgb(43, 72, 61), Typeface.BOLD);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(-1, -2);
        labelParams.topMargin = dp(24);
        card.addView(fieldLabel, labelParams);
        addressInput = new EditText(this);
        addressInput.setSingleLine(true);
        addressInput.setTextSize(15);
        addressInput.setTextColor(Color.rgb(21, 54, 43));
        addressInput.setHint("http://192.168.0.113:8765");
        addressInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addressInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        addressInput.setPadding(dp(13), 0, dp(13), 0);
        addressInput.setBackground(outlined());
        addressInput.setOnEditorActionListener((view, action, event) -> {
            if (action == EditorInfo.IME_ACTION_GO) {
                connect();
                return true;
            }
            return false;
        });
        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(-1, dp(52));
        inputParams.topMargin = dp(9);
        card.addView(addressInput, inputParams);

        connectButton = new Button(this);
        connectButton.setText("连接账本");
        connectButton.setAllCaps(false);
        connectButton.setTextSize(16);
        connectButton.setTextColor(Color.WHITE);
        connectButton.setBackground(rounded(BRAND, dp(12)));
        connectButton.setOnClickListener(view -> connect());
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(-1, dp(52));
        buttonParams.topMargin = dp(17);
        card.addView(connectButton, buttonParams);

        errorText = label("", 13, Color.rgb(164, 55, 44), Typeface.NORMAL);
        errorText.setVisibility(View.GONE);
        LinearLayout.LayoutParams errorParams = new LinearLayout.LayoutParams(-1, -2);
        errorParams.topMargin = dp(12);
        card.addView(errorText, errorParams);

        TextView footer = label("账目保存在电脑。电脑关机、休眠或安全退出后，手机暂时无法访问。",
                12, Color.rgb(118, 133, 124), Typeface.NORMAL);
        footer.setLineSpacing(dp(3), 1);
        LinearLayout.LayoutParams footerParams = new LinearLayout.LayoutParams(-1, -2);
        footerParams.topMargin = dp(25);
        content.addView(footer, footerParams);
    }

    private void addInstruction(LinearLayout parent, String value) {
        TextView item = label(value, 13, Color.rgb(91, 108, 98), Typeface.NORMAL);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(13);
        parent.addView(item, params);
    }

    @SuppressLint("SetJavaScriptEnabled") // The bundled ledger UI needs JavaScript; navigation stays on the verified origin.
    private void createWebScreen(FrameLayout parent) {
        webScreen = new FrameLayout(this);
        webScreen.setVisibility(View.GONE);
        parent.addView(webScreen, new FrameLayout.LayoutParams(-1, -1));
        webView = new WebView(this);
        webView.setBackgroundColor(BACKGROUND);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSafeBrowsingEnabled(true);
        settings.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.addJavascriptInterface(new PrintBridge(), "XiaoGuoAndroid");
        webView.setWebViewClient(new LedgerClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                                        FileChooserParams params) {
                if (uploadCallback != null) uploadCallback.onReceiveValue(null);
                uploadCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), PICK_UPLOAD);
                    return true;
                } catch (ActivityNotFoundException error) {
                    uploadCallback = null;
                    callback.onReceiveValue(null);
                    toast("找不到可选择图片的应用");
                    return true;
                }
            }

            @Override public void onProgressChanged(WebView view, int value) {
                progress.setVisibility(value < 100 ? View.VISIBLE : View.GONE);
            }
        });
        webView.setDownloadListener(downloadListener());
        webScreen.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        createOfflineOverlay();
    }

    private void createOfflineOverlay() {
        offlineOverlay = new LinearLayout(this);
        offlineOverlay.setOrientation(LinearLayout.VERTICAL);
        offlineOverlay.setGravity(Gravity.CENTER);
        offlineOverlay.setPadding(dp(26), dp(25), dp(26), dp(25));
        offlineOverlay.setBackgroundColor(BACKGROUND);
        offlineOverlay.setVisibility(View.GONE);
        webScreen.addView(offlineOverlay, new FrameLayout.LayoutParams(-1, -1));
        offlineOverlay.addView(label("连接中断", 25, BRAND, Typeface.BOLD));
        TextView hint = label("请确认电脑仍在运行、手机连接了相同 Wi-Fi，然后重试。",
                14, Color.rgb(88, 108, 96), Typeface.NORMAL);
        hint.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams hintParams = new LinearLayout.LayoutParams(-1, -2);
        hintParams.topMargin = dp(12);
        offlineOverlay.addView(hint, hintParams);

        Button retry = new Button(this);
        retry.setText("重新连接");
        retry.setAllCaps(false);
        retry.setTextColor(Color.WHITE);
        retry.setBackground(rounded(BRAND, dp(11)));
        retry.setOnClickListener(view -> {
            offlineOverlay.setVisibility(View.GONE);
            webView.reload();
        });
        LinearLayout.LayoutParams retryParams = new LinearLayout.LayoutParams(-1, dp(48));
        retryParams.topMargin = dp(22);
        offlineOverlay.addView(retry, retryParams);

        Button change = new Button(this);
        change.setText("更换电脑地址");
        change.setAllCaps(false);
        change.setTextColor(BRAND);
        change.setBackground(rounded(Color.WHITE, dp(11)));
        change.setOnClickListener(view -> showConnection());
        LinearLayout.LayoutParams changeParams = new LinearLayout.LayoutParams(-1, dp(48));
        changeParams.topMargin = dp(10);
        offlineOverlay.addView(change, changeParams);
    }

    private void connect() {
        ConnectionAddress candidate;
        try {
            candidate = ConnectionAddress.parse(addressInput.getText().toString());
        } catch (IllegalArgumentException error) {
            showError(error.getMessage());
            return;
        }
        int generation = ++connectGeneration;
        errorText.setVisibility(View.GONE);
        connectButton.setEnabled(false);
        connectButton.setText("正在连接…");
        worker.execute(() -> {
            String failure = checkLedger(candidate);
            runOnUiThread(() -> {
                if (generation != connectGeneration || isDestroyed()) return;
                connectButton.setEnabled(true);
                connectButton.setText("连接账本");
                if (failure != null) {
                    showError(failure);
                    return;
                }
                activeAddress = candidate;
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                        .putString(SAVED_URL, candidate.baseUrl()).apply();
                hostText.setText(candidate.hostLabel());
                connectionScreen.setVisibility(View.GONE);
                webScreen.setVisibility(View.VISIBLE);
                changeAddress.setVisibility(View.VISIBLE);
                offlineOverlay.setVisibility(View.GONE);
                webView.loadUrl(candidate.baseUrl());
            });
        });
    }

    private String checkLedger(ConnectionAddress address) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(address.baseUrl() + "/api/status").openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            connection.setRequestProperty("Accept", "application/json");
            if (connection.getResponseCode() != 200) return "电脑已回应，但不是可用的小锅账本服务";
            try (InputStream input = connection.getInputStream()) {
                ByteArrayOutputStream body = new ByteArrayOutputStream();
                byte[] bytes = new byte[1024];
                int length;
                while ((length = input.read(bytes)) != -1) {
                    body.write(bytes, 0, length);
                    if (body.size() > 16384) return "电脑返回的内容不是账本状态";
                }
                if (body.size() == 0) return "电脑返回了空白页面，请核对地址";
                JSONObject status = new JSONObject(body.toString("UTF-8"));
                String mode = status.optString("auth_mode");
                if (!status.has("version") || !(mode.equals("direct") || mode.equals("password"))) {
                    return "这个地址不是小锅账本，请使用电脑菜单里的手机地址";
                }
                return null;
            }
        } catch (Exception error) {
            return "连接失败。请确认电脑服务正在运行、手机与电脑处于同一 Wi-Fi，并核对地址。";
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void showConnection() {
        ++connectGeneration;
        connectButton.setEnabled(true);
        connectButton.setText("连接账本");
        connectionScreen.setVisibility(View.VISIBLE);
        webScreen.setVisibility(View.GONE);
        changeAddress.setVisibility(View.GONE);
        progress.setVisibility(View.GONE);
    }

    private void showError(String message) {
        errorText.setText(message);
        errorText.setVisibility(View.VISIBLE);
    }

    private DownloadListener downloadListener() {
        return (url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (activeAddress == null || !activeAddress.sameOrigin(url)) {
                toast("只允许保存当前账本提供的文件");
                return;
            }
            if (pendingDownloadUrl != null) {
                toast("请先完成当前文件的保存");
                return;
            }
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType)
                    .replaceAll("[\\\\/:*?\"<>|]", "_");
            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
            save.addCategory(Intent.CATEGORY_OPENABLE);
            save.setType(mimeType == null || mimeType.isEmpty() ? "application/octet-stream" : mimeType);
            save.putExtra(Intent.EXTRA_TITLE, name);
            pendingDownloadUrl = url;
            try {
                startActivityForResult(save, SAVE_DOWNLOAD);
            } catch (ActivityNotFoundException error) {
                pendingDownloadUrl = null;
                toast("找不到可保存文件的应用");
            }
        };
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == PICK_UPLOAD) {
            if (uploadCallback != null) {
                uploadCallback.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                uploadCallback = null;
            }
        } else if (requestCode == SAVE_DOWNLOAD) {
            String url = pendingDownloadUrl;
            pendingDownloadUrl = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null && url != null) {
                downloadTo(url, data.getData());
            }
        }
    }

    private void downloadTo(String url, Uri destination) {
        String cookie = CookieManager.getInstance().getCookie(url);
        toast("正在保存文件…");
        worker.execute(() -> {
            HttpURLConnection connection = null;
            File temporary = null;
            String result;
            try {
                connection = (HttpURLConnection) new URL(url).openConnection();
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(30000);
                if (cookie != null) connection.setRequestProperty("Cookie", cookie);
                if (connection.getResponseCode() != 200) throw new Exception("下载请求未成功");
                temporary = File.createTempFile("ledger-download-", ".tmp", getCacheDir());
                try (InputStream input = connection.getInputStream();
                     OutputStream output = new FileOutputStream(temporary)) {
                    copy(input, output);
                }
                try (InputStream input = new FileInputStream(temporary);
                     OutputStream output = getContentResolver().openOutputStream(destination, "w")) {
                    if (output == null) throw new Exception("所选位置不能写入");
                    copy(input, output);
                }
                result = "文件已保存到所选位置";
            } catch (Exception error) {
                result = "保存失败，请检查电脑连接和目标文件夹后重试";
            } finally {
                if (temporary != null) temporary.delete();
                if (connection != null) connection.disconnect();
            }
            String finalResult = result;
            runOnUiThread(() -> {
                if (!isDestroyed()) toast(finalResult);
            });
        });
    }

    private static void copy(InputStream input, OutputStream output) throws Exception {
        byte[] buffer = new byte[65536];
        int count;
        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
    }

    private final class LedgerClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (!request.isForMainFrame()) return false;
            String url = request.getUrl().toString();
            if (activeAddress != null && activeAddress.sameOrigin(url)) return false;
            String scheme = request.getUrl().getScheme();
            if ("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme)) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (ActivityNotFoundException error) {
                    toast("找不到可打开链接的浏览器");
                }
            }
            return true;
        }

        @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
            navigationFailed = false;
            progress.setVisibility(View.VISIBLE);
        }

        @Override public void onPageFinished(WebView view, String url) {
            progress.setVisibility(View.GONE);
            if (!navigationFailed) offlineOverlay.setVisibility(View.GONE);
            if (activeAddress != null && activeAddress.sameOrigin(url)) {
                view.evaluateJavascript(
                        "window.print = function(){ XiaoGuoAndroid.printPage(); };", null);
            }
        }

        @Override public void onReceivedError(WebView view, WebResourceRequest request,
                                              WebResourceError error) {
            if (request.isForMainFrame()) {
                navigationFailed = true;
                offlineOverlay.setVisibility(View.VISIBLE);
            }
        }

        @Override public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                                  WebResourceResponse error) {
            if (request.isForMainFrame() && error.getStatusCode() >= 400) {
                navigationFailed = true;
                offlineOverlay.setVisibility(View.VISIBLE);
            }
        }

        @Override public void onReceivedSslError(WebView view, SslErrorHandler handler,
                                                 SslError error) {
            handler.cancel();
            navigationFailed = true;
            offlineOverlay.setVisibility(View.VISIBLE);
        }
    }

    private final class PrintBridge {
        @JavascriptInterface public void printPage() {
            runOnUiThread(() -> {
                if (activeAddress == null || !activeAddress.sameOrigin(webView.getUrl())) return;
                PrintManager manager = (PrintManager) getSystemService(Context.PRINT_SERVICE);
                if (manager != null) manager.print("小锅账本", webView.createPrintDocumentAdapter("小锅账本"), null);
            });
        }
    }

    private void handleBack() {
        if (webScreen.getVisibility() == View.VISIBLE && webView.canGoBack()) {
            webView.goBack();
        } else if (connectionScreen.getVisibility() == View.VISIBLE && activeAddress != null) {
            connectionScreen.setVisibility(View.GONE);
            webScreen.setVisibility(View.VISIBLE);
            changeAddress.setVisibility(View.VISIBLE);
        } else {
            finish();
        }
    }

    @SuppressLint("GestureBackNavigation") // API 33+ uses OnBackInvokedDispatcher above.
    @Override public void onBackPressed() {
        handleBack();
    }

    @Override protected void onDestroy() {
        ++connectGeneration;
        if (uploadCallback != null) uploadCallback.onReceiveValue(null);
        worker.shutdownNow();
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
        }
        super.onDestroy();
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private TextView label(String text, int sp, int color, int weight) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setTypeface(Typeface.DEFAULT, weight);
        return view;
    }

    private GradientDrawable rounded(int color, int radius) {
        GradientDrawable result = new GradientDrawable();
        result.setColor(color);
        result.setCornerRadius(radius);
        return result;
    }

    private GradientDrawable outlined() {
        GradientDrawable result = rounded(Color.WHITE, dp(11));
        result.setStroke(dp(1), Color.rgb(209, 225, 213));
        return result;
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }
}
