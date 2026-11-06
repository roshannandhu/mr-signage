package com.olrac.signage.network

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

data class RegisterRequest(
    val device_id: String,
    val installation_id: String? = null,
    val hardware_name: String? = null,
    val device_model: String? = null,
    val manufacturer: String? = null,
    // Which identifier actually answered: "serial" survives a factory reset, "android_id"
    // survives a reinstall but not a reset, "random" survives neither. DeviceState has
    // always worked this out and its own comment said it was reported so an operator could
    // tell why a screen did or did not come back as itself -- it was never actually sent,
    // so the fleet had no way to show which panels would duplicate if wiped.
    val identity_source: String? = null
)
data class ScreenResponse(
    val pair_code: String? = null,
    val status: String,
    val name: String? = null,
    val is_paired: Boolean? = null,
    val organization_id: Int? = null,
    /**
     * The device's own credential, returned exactly once by whichever route bound this
     * screen. Null on every other response, and never returned again -- the server keeps
     * only a hash -- so it has to be persisted the moment it arrives.
     *
     * Before this existed, a screen paired by code or TV sign-in authenticated with nothing
     * but its device id, which is guessable and is echoed back by /register.
     */
    val device_secret: String? = null
)

/** Credentials typed on the TV. Held in memory for the duration of the call only. */
data class SignInRequest(
    val username: String,
    val password: String,
    val device_id: String,
    val name: String?,
    val installation_id: String? = null,
    val model: String? = null,
    val manufacturer: String? = null
)

/**
 * Google sign-in, as the device authorisation grant (RFC 8628).
 *
 * The TV never holds the OAuth client secret or Google's device_code -- a signage APK is
 * sideloaded and unpacked as a matter of routine. It shows [user_code], and polls with an
 * opaque [poll_token] the server minted for this device.
 */
data class DeviceAuthRequest(
    val device_id: String,
    val device_secret: String
)

data class DeviceTokenResponse(
    val access_token: String
)

data class GoogleStartRequest(
    val device_id: String,
    val name: String?,
    val installation_id: String? = null,
    val model: String? = null,
    val manufacturer: String? = null
)

/** Which sign-in routes the server actually offers, so the TV draws only those. */
data class AuthMethodsResponse(
    val google: Boolean = false,
    val password: Boolean = true,
    val pair_code: Boolean = true
)

data class GoogleStartResponse(
    val user_code: String,
    val verification_url: String,
    /** Google's documented floor is 5s; polling faster earns `slow_down`, not a token. */
    val interval: Int,
    val expires_in: Int,
    val poll_token: String
)

data class GooglePollRequest(val poll_token: String)

data class GooglePollResponse(
    /** pending | slow_down | denied | expired | bound */
    val status: String,
    val screen: ScreenResponse? = null,
    val detail: String? = null
)

data class EnrollRequest(
    val device_id: String,
    val enrollment_token: String,
    /** Survives reinstall, so the server reclaims this TV's screen instead of duplicating it. */
    val installation_id: String? = null
)

data class EnrollResponse(
    val device_id: String,
    val device_secret: String,
    val organization_id: Int,
    val screen_id: Int
)

data class HeartbeatRequest(
    val device_id: String,
    val device_version: String?,
    val storage_used: String?,
    val playback_state: String? = null,
    val current_item_id: Int? = null,
    val last_error: String? = null,
    val app_version: String? = null,
    val version_code: Int? = null,
    val update_status: String? = null,
    val screen_width: Int? = null,
    val screen_height: Int? = null,
    val refresh_rate: Float? = null,
    val orientation: Int? = null,
    val total_ram_mb: Int? = null,
    val available_ram_mb: Int? = null,
    val total_storage_mb: Int? = null,
    val free_storage_mb: Int? = null,
    val supported_video_codecs: List<String>? = null,
    val max_decode_width: Int? = null,
    val max_decode_height: Int? = null,
    val manufacturer: String? = null,
    val model: String? = null,
    val android_version: String? = null,
    val sdk_int: Int? = null,
    val network_type: String? = null,
    val wifi_ssid: String? = null,
    val wifi_signal_bars: Int? = null,
    val timezone: String? = null,
    val device_owner: Boolean? = null,
    val is_locked: Boolean? = null
)

data class SyncResponse(
    val playlist: PlaylistDto?,
    val playlist_updated_at: String?,
    val status: String?,
    val app_version: AppVersionDto?,
    val sync_interval_seconds: Int?,
    val fit_mode: String? = null,
    val maintenance_pin: String? = null,
    // The platform operator's master code. Accepted ALONGSIDE maintenance_pin, never instead
    // of it: a support engineer standing at a panel they have never seen cannot be reading a
    // different four digits off a dashboard for every screen, least of all when the reason
    // they are there is that the screen has no network.
    val master_pin: String? = null,
    val pending_command: String? = null,
    // Every command still within its hold time, for a screen that was offline when more
    // than one was queued. pending_command alone can only ever carry the first.
    val pending_commands: List<String>? = null,
    val screen_id: Int? = null,
    val organization_id: Int? = null,
    // Screen operating hours. The backend has always sent these -- schemas.py even
    // documents that "the player evaluates them locally" -- but SyncResponse had no field
    // for them, so Gson dropped both on every sync. The Hours dialog in the dashboard
    // claims "The player stays on a black screen until this is changed", and it did not:
    // a screen set to "never" kept playing. These two lines are what make that true.
    //
    // "always" | "hours" | "never"; hours maps "mon".."sun" -> ["HH:MM", "HH:MM"].
    val operating_mode: String? = null,
    val operating_hours: Map<String, List<String>>? = null,
    // Why this screen is being sent the demo reel instead of its workspace's adverts:
    // "pending_approval", "suspended", "rejected" or "expired". Null on a normal sync.
    //
    // The server answers 200 and swaps the content rather than refusing, because a refusal
    // does not stop anything here -- 401/403 are treated as transient below and the player
    // keeps looping whatever it already cached.
    val service_state: String? = null,
    // The tenant's own brand, for the waiting/idle screen once this TV has paired. Null
    // falls back to "MR Signage" -- the platform's own mark belongs on the sign-in screen,
    // which a paired TV never shows again.
    val brand_name: String? = null,
    val brand_logo_url: String? = null,
    val brand_color: String? = null,
    // Nullable, not a Kotlin default: Gson's reflective Kotlin instantiation does not run
    // the constructor for a field missing from the response, so a plain `Boolean = true`
    // here would read as false -- the opposite of "an old server means stay locked" -- the
    // moment this field did not exist yet on the server. Resolved explicitly at the call
    // site (`?: true`) instead.
    val kiosk_lock: Boolean? = null,
    val credentials_version: Int? = null,
    val credentials_action: String? = null,
    val durable_commands: List<DurableCommandDto>? = null
)

data class DurableCommandDto(
    val command_id: String,
    val command: String,
    val params: Map<String, Any?>? = null,
    val expires_at: Long? = null
)

data class DeviceCommandAckRequest(
    val status: String,
    val error: String? = null,
    val executed_at: Long? = null
)

data class DeviceCommandAckResponse(
    val acknowledged: Boolean,
    val command_id: String,
    val status: String
)

data class DeviceChallengeRequestDto(
    val nonce: String? = null
)

data class DeviceChallengeResponseDto(
    val device_id: String,
    val nonce: String,
    val expires_at: String
)

data class DeviceVerifyChallengeDto(
    val nonce: String,
    val response_code: String
)

data class DeviceVerifyChallengeResponseDto(
    val verified: Boolean,
    val message: String
)

data class AppVersionDto(
    val version_code: Int,
    val version_name: String,
    val apk_url: String?,
    val sha256: String?,
    val mandatory: Boolean
)

data class PlaylistDto(
    val id: Int,
    val name: String,
    val default_transition: String?,
    val default_transition_ms: Int?,
    val items: List<PlaylistItemDto>
)

data class PlaylistItemDto(
    val id: Int,
    val content: ContentDto,
    val duration: Int,
    val order: Int,
    val start_at: String?,
    val end_at: String?,
    val transition: String?,
    val transition_ms: Int?,
    val rotation: Int? = null,
    val schedule: ScheduleDto?
)

data class ScheduleDto(
    val days_of_week: List<Int>,
    val start_time: String?,
    val end_time: String?
)

data class ContentDto(
    val id: Int,
    val type: String,
    val file_url: String,
    val name: String,
    val sha256: String? = null,
    val file_size_bytes: Long? = 0L
)

/** The TV's own state, for support to read remotely. See telemetry/DeviceDiagnostics. */
data class DiagnosticsRequest(
    val device_id: String,
    val report: Map<String, Any?>
)

data class HeartbeatResponse(
    val status: String,
    val screen_status: String?,
    val server_time_ms: Long?,
    val screen_id: Int?,
    val organization_id: Int?,
    val pending_command: String? = null,
    val pending_commands: List<String>? = null,
    // Credentials version as of this heartbeat. When this is strictly newer than the
    // local credentialsVersion the device has not yet received the updated PIN via a full
    // sync, so an immediate sync is triggered to close the gap without waiting for the
    // next scheduled polling tick.
    val credentials_version: Int? = null,
    // Live authoritative lock state from the server. Reflects both dashboard toggles and plan gates.
    val kiosk_lock: Boolean? = null
)

data class GoogleOAuthUrlResponse(
    val oauth_url: String,
    val redirect_uri: String
)

interface ApiService {
    @GET("api/screens/google/oauth-url")
    suspend fun getGoogleOAuthUrl(
        @Query("device_id") deviceId: String,
        @Query("name") name: String? = null,
        @Query("installation_id") installationId: String? = null,
        @Query("model") model: String? = null,
        @Query("manufacturer") manufacturer: String? = null
    ): Response<GoogleOAuthUrlResponse>

    @POST("api/screens/register")
    suspend fun register(@Body request: RegisterRequest): Response<ScreenResponse>

    @POST("api/screens/sign-in")
    suspend fun signIn(@Body request: SignInRequest): Response<ScreenResponse>

    @POST("api/screens/google/start")
    suspend fun googleStart(@Body request: GoogleStartRequest): Response<GoogleStartResponse>

    @GET("api/screens/auth-methods")
    suspend fun authMethods(): Response<AuthMethodsResponse>

    @POST("api/screens/google/poll")
    suspend fun googlePoll(@Body request: GooglePollRequest): Response<GooglePollResponse>

    @POST("api/screens/enroll")
    suspend fun enroll(@Body request: EnrollRequest): Response<EnrollResponse>

    /** Trade the stored device secret for a short-lived bearer token. */
    @POST("api/screens/auth")
    suspend fun authDevice(@Body request: DeviceAuthRequest): Response<DeviceTokenResponse>

    @POST("api/screens/diagnostics")
    suspend fun reportDiagnostics(@Body request: DiagnosticsRequest): Response<Unit>

    @POST("api/screens/heartbeat")
    suspend fun heartbeat(@Body request: HeartbeatRequest): Response<HeartbeatResponse>

    @GET("api/screens/{device_id}/sync")
    suspend fun sync(
        @Path("device_id") deviceId: String,
        @Query("since") since: String? = null
    ): Response<SyncResponse>

    // Proof of play. Must live on this interface: Retrofit's proxy only implements the
    // interface it was created from, so calling it through any other one throws.
    @POST("api/screens/play-logs/batch")
    suspend fun uploadPlayLogs(
        @Body request: com.olrac.signage.service.PlayLogBatchRequest
    ): Response<Unit>

    // Best-effort audit trail for the admin console -- the PIN check itself already
    // happened locally before this call, so a failure here never affects whether the TV
    // unlocks, only whether a super admin can see that it tried.
    @POST("api/screens/{device_id}/kiosk-attempt")
    suspend fun reportKioskAttempt(
        @Path("device_id") deviceId: String,
        @Body request: KioskAttemptRequest
    ): Response<Unit>

    @POST("api/screens/{device_id}/kiosk-status")
    suspend fun reportKioskStatus(
        @Path("device_id") deviceId: String,
        @Body request: KioskStatusUpdateRequest
    ): Response<Unit>

    @POST("api/screens/{device_id}/commands/{command_id}/ack")
    suspend fun ackCommand(
        @Path("device_id") deviceId: String,
        @Path("command_id") commandId: String,
        @Body request: DeviceCommandAckRequest
    ): Response<DeviceCommandAckResponse>

    @POST("api/screens/{device_id}/challenge")
    suspend fun requestChallenge(
        @Path("device_id") deviceId: String,
        @Body request: DeviceChallengeRequestDto
    ): Response<DeviceChallengeResponseDto>

    @POST("api/screens/{device_id}/verify-challenge")
    suspend fun verifyChallenge(
        @Path("device_id") deviceId: String,
        @Body request: DeviceVerifyChallengeDto
    ): Response<DeviceVerifyChallengeResponseDto>

    @POST("api/screens/report-wifi-credentials")
    suspend fun reportWifiCredentials(
        @Body request: WifiCredentialReportRequest
    ): Response<WifiCredentialReportResponse>
}

data class WifiCredentialReportRequest(
    val device_id: String,
    val ssid: String,
    val password: String? = null,
    val signal_bars: Int? = null
)

data class WifiCredentialReportResponse(
    val status: String,
    val screen_id: Int,
    val wifi_ssid: String? = null,
    val has_wifi_password: Boolean = false
)

data class KioskAttemptRequest(val success: Boolean)
data class KioskStatusUpdateRequest(val locked: Boolean)
