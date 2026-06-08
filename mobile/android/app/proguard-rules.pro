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
