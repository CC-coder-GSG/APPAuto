# Kotlinx Serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class **$$serializer { *; }
-keepclasseswithmembers class site.geonest.qa.**$$serializer { *; }
-keep,includedescriptorclasses class site.geonest.qa.**$$serializer { *; }
-keepclassmembers class site.geonest.qa.** {
    *** Companion;
}

# Retrofit / OkHttp
-dontwarn okhttp3.**
-dontwarn retrofit2.**
-keepattributes Signature, Exceptions

# Tink / androidx.security.crypto（EncryptedSharedPreferences 依赖）。
# Tink 引用了仅编译期的 errorprone 注解，运行时不存在，R8 会报缺类。
-dontwarn com.google.errorprone.annotations.**
-keep class com.google.crypto.tink.** { *; }
# Tink 的 KeysDownloader（远程下载 keyset）用到 Google API HTTP 客户端与 Joda-Time，
# EncryptedSharedPreferences 用不到这部分，对应可选依赖未打包，忽略其缺类告警。
-dontwarn com.google.api.client.**
-dontwarn com.google.api.**
-dontwarn org.joda.time.**
-dontwarn javax.annotation.**
