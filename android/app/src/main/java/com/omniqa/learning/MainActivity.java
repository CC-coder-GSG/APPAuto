package com.omniqa.learning;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
    private static final String PREFS = "omniqa_settings";
    private static final String SERVER_URL = "server_url";
    private WebView webView;
    private String configuredBase;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
        }
        configuredBase = getSharedPreferences(PREFS, MODE_PRIVATE).getString(SERVER_URL, "");
        if (configuredBase.isEmpty()) showServerSettings(); else showWebApp();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private TextView text(String value, int size) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(Color.rgb(23, 32, 51));
        return view;
    }

    private void applySystemBarInsets(View root, int horizontalDp, int verticalDp) {
        int horizontal = dp(horizontalDp);
        int vertical = dp(verticalDp);
        root.setOnApplyWindowInsetsListener((view, windowInsets) -> {
            int left;
            int top;
            int right;
            int bottom;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Insets bars = windowInsets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                left = bars.left;
                top = bars.top;
                right = bars.right;
                bottom = bars.bottom;
            } else {
                left = windowInsets.getSystemWindowInsetLeft();
                top = windowInsets.getSystemWindowInsetTop();
                right = windowInsets.getSystemWindowInsetRight();
                bottom = windowInsets.getSystemWindowInsetBottom();
            }
            view.setPadding(horizontal + left, vertical + top, horizontal + right, vertical + bottom);
            return windowInsets;
        });
        root.requestApplyInsets();
    }

    private void disposeWebView() {
        if (webView == null) return;
        webView.stopLoading();
        webView.removeJavascriptInterface("AndroidApp");
        webView.destroy();
        webView = null;
    }

    private LinearLayout baseMessageLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        root.setBackgroundColor(Color.rgb(245, 247, 251));
        applySystemBarInsets(root, 24, 24);
        return root;
    }

    private void showServerSettings() {
        disposeWebView();
        LinearLayout root = baseMessageLayout();

        TextView title = text("连接 OmniQA 服务器", 26);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(title);
        TextView tip = text("请输入手机能够访问的系统地址。使用 FRP 时应填写外网映射地址，而不是内网地址或 FRP 控制端口。", 15);
        tip.setPadding(0, dp(10), 0, dp(18));
        root.addView(tip);

        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("例如 https://qa.example.com");
        input.setText(configuredBase);
        input.setTextSize(16);
        root.addView(input, new LinearLayout.LayoutParams(-1, dp(56)));

        Button save = new Button(this);
        save.setText("保存并连接");
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(-1, dp(54));
        buttonParams.topMargin = dp(16);
        root.addView(save, buttonParams);
        save.setOnClickListener(v -> {
            String value = input.getText().toString().trim();
            while (value.endsWith("/")) value = value.substring(0, value.length() - 1);
            Uri parsed = Uri.parse(value);
            boolean validScheme = "http".equalsIgnoreCase(parsed.getScheme()) || "https".equalsIgnoreCase(parsed.getScheme());
            String path = parsed.getPath();
            boolean rootPath = path == null || path.isEmpty();
            if (!validScheme || parsed.getHost() == null || !rootPath || parsed.getQuery() != null || parsed.getFragment() != null) {
                Toast.makeText(this, "请输入完整的服务器根地址，例如 https://qa.example.com，不要附加 /mobile", Toast.LENGTH_LONG).show();
                return;
            }
            configuredBase = value;
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(SERVER_URL, value).apply();
            showWebApp();
        });
        setContentView(root);
    }

    private void showConnectionError(String detail) {
        disposeWebView();
        LinearLayout root = baseMessageLayout();

        TextView title = text("无法连接服务器", 26);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(title);
        TextView address = text(configuredBase, 16);
        address.setTextColor(Color.rgb(79, 70, 229));
        address.setPadding(0, dp(12), 0, dp(8));
        root.addView(address);
        TextView tip = text("请检查外网映射、端口、防火墙和服务器地址。" + (detail.isEmpty() ? "" : "\n\n" + detail), 14);
        tip.setPadding(0, 0, 0, dp(18));
        root.addView(tip);

        Button retry = new Button(this);
        retry.setText("重新连接");
        root.addView(retry, new LinearLayout.LayoutParams(-1, dp(54)));
        retry.setOnClickListener(v -> showWebApp());

        Button change = new Button(this);
        change.setText("修改服务器地址");
        LinearLayout.LayoutParams changeParams = new LinearLayout.LayoutParams(-1, dp(54));
        changeParams.topMargin = dp(12);
        root.addView(change, changeParams);
        change.setOnClickListener(v -> showServerSettings());
        setContentView(root);
    }

    @SuppressLint({"SetJavaScriptEnabled", "JavascriptInterface"})
    private void showWebApp() {
        disposeWebView();
        FrameLayout safeArea = new FrameLayout(this);
        safeArea.setBackgroundColor(Color.rgb(245, 247, 251));
        applySystemBarInsets(safeArea, 0, 0);

        webView = new WebView(this);
        WebView loadingView = webView;
        webView.setBackgroundColor(Color.rgb(245, 247, 251));
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setDatabaseEnabled(true);
        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new AppBridge(), "AndroidApp");
        webView.setWebViewClient(new WebViewClient() {
            private boolean mainFrameFailed = false;

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri target = request.getUrl();
                Uri server = Uri.parse(configuredBase);
                boolean sameOrigin = target.getScheme() != null && target.getScheme().equalsIgnoreCase(server.getScheme())
                    && target.getHost() != null && target.getHost().equalsIgnoreCase(server.getHost())
                    && target.getPort() == server.getPort();
                if (sameOrigin) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, target)); }
                catch (Exception error) { Toast.makeText(MainActivity.this, "无法打开外部链接", Toast.LENGTH_SHORT).show(); }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (!request.isForMainFrame() || mainFrameFailed) return;
                mainFrameFailed = true;
                String detail = error.getDescription() == null ? "" : error.getDescription().toString();
                view.post(() -> {
                    if (loadingView == webView) showConnectionError(detail);
                });
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
                if (!request.isForMainFrame() || mainFrameFailed || errorResponse.getStatusCode() < 400) return;
                mainFrameFailed = true;
                String detail = "服务器返回 HTTP " + errorResponse.getStatusCode() + "，请确认该地址能够访问 /mobile。";
                view.post(() -> {
                    if (loadingView == webView) showConnectionError(detail);
                });
            }
        });
        safeArea.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        setContentView(safeArea);
        webView.loadUrl(configuredBase + "/mobile");
    }

    public class AppBridge {
        @JavascriptInterface
        public void openSettings() {
            runOnUiThread(() -> showServerSettings());
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        disposeWebView();
        super.onDestroy();
    }
}
