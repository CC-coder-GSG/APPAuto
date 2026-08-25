package site.geonest.qa.core.data

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 普通偏好存储（非敏感的界面选择，如软件/版本/视图等下拉记忆）。
 * 与 [TokenStore] 区分：令牌走加密存储，这里只存可重建的 UI 选择。
 */
@Singleton
class PreferenceStore @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val prefs = context.getSharedPreferences("qa_ui_prefs", Context.MODE_PRIVATE)

    fun getInt(key: String): Int? = if (prefs.contains(key)) prefs.getInt(key, 0) else null

    fun putInt(key: String, value: Int?) {
        prefs.edit().apply { if (value == null) remove(key) else putInt(key, value) }.apply()
    }

    fun getString(key: String): String? = prefs.getString(key, null)

    fun putString(key: String, value: String?) {
        prefs.edit().apply { if (value == null) remove(key) else putString(key, value) }.apply()
    }
}
