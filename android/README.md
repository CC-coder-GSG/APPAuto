# OmniQA 安卓学习答题端

这是学习中心的安卓客户端。它加载服务端专用的 `/mobile` 页面，复用现有账号、题库、唯一提交和成绩数据，因此网页与 App 不会产生两套答卷。

## 构建

```powershell
cd android
.\gradlew.bat assembleDebug
```

输出文件：`app/build/outputs/apk/debug/app-debug.apk`。

首次打开 App 时填写服务端地址，例如 `http://192.168.1.20:8000`。手机与服务器需要网络互通；生产环境建议通过 HTTPS 访问。

使用 FRP 时，如果 `外网IP:18000` 已映射到内网 APPAuto 的 `8000` 端口，填写 `http://外网IP:18000`；如果外网 Nginx 已提供 HTTPS 域名，填写类似 `https://qa.example.com`。不要填写 FRP 的控制端口，也不要在地址后添加 `/mobile`。
