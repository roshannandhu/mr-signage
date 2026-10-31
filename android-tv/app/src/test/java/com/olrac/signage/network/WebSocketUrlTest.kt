package com.olrac.signage.network

import org.junit.Assert.assertEquals
import org.junit.Test

class WebSocketUrlTest {

    @Test
    fun `https becomes wss and the route keeps its trailing ws`() {
        assertEquals(
            "wss://api.example.com/api/ws/tv-1/ws",
            webSocketUrl("https://api.example.com/", "tv-1", null),
        )
    }

    @Test
    fun `http becomes ws`() {
        assertEquals("ws://10.0.0.5:8000/api/ws/tv-1/ws", webSocketUrl("http://10.0.0.5:8000", "tv-1", null))
    }

    @Test
    fun `a token is presented as a query parameter`() {
        assertEquals(
            "wss://api.example.com/api/ws/tv-1/ws?token=aaa.bbb.ccc",
            webSocketUrl("https://api.example.com", "tv-1", "aaa.bbb.ccc"),
        )
    }

    @Test
    fun `no token and a blank token both mean legacy`() {
        assertEquals("wss://h/api/ws/tv-1/ws", webSocketUrl("https://h", "tv-1", ""))
        assertEquals("wss://h/api/ws/tv-1/ws", webSocketUrl("https://h", "tv-1", "   "))
    }

    @Test
    fun `a token with unsafe characters is encoded`() {
        assertEquals("wss://h/api/ws/tv-1/ws?token=a%26b%3Dc", webSocketUrl("https://h", "tv-1", "a&b=c"))
    }
}
