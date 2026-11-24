// The night over the 2D showcase's harbour: deep blue to a misty horizon, twinkling stars,
// a moon behind rolling rain cloud that parts as `clear` rises. Drawn on one screen-sized
// rect; UV is 0 to 1 across the screen.
shader_type canvas_item;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 zenith : source_color = vec3(0.04, 0.065, 0.16);
uniform vec3 middle : source_color = vec3(0.09, 0.14, 0.28);
uniform vec3 horizon : source_color = vec3(0.26, 0.31, 0.44);
uniform vec3 cloud_shadow : source_color = vec3(0.03, 0.04, 0.075);
uniform vec3 cloud_lit : source_color = vec3(0.5, 0.55, 0.68);
uniform vec3 moon_color : source_color = vec3(1.0, 0.96, 0.86);
uniform vec2 moon = vec2(0.85, 0.16);
uniform float horizon_y = 0.64;
uniform float aspect = 1.7778;
uniform float clear : hint_range(0.0, 1.0) = 0.0;

void fragment() {
	vec2 p = vec2(UV.x * aspect, UV.y);
	float h = clamp(UV.y / horizon_y, 0.0, 1.0);
	vec3 sky = mix(zenith, middle, smoothstep(0.0, 0.6, h));
	sky = mix(sky, horizon, smoothstep(0.55, 1.0, h));
	// Stars: one chance per cell, each with its own twinkle.
	vec2 grid = p * 150.0;
	vec2 cell = floor(grid);
	float pick = hash21(cell);
	vec2 at = cell + 0.5 + (vec2(hash21(cell + 7.1), hash21(cell + 3.3)) - 0.5) * 0.7;
	float star = step(0.984, pick) * smoothstep(0.2, 0.0, length(grid - at));
	star *= 0.55 + 0.45 * sin(TIME * (1.5 + pick * 4.0) + pick * 40.0);
	star *= 1.0 - smoothstep(0.45, 0.95, h);
	sky += vec3(0.85, 0.92, 1.0) * star * 1.6;
	// The moon: a pale disc with darker seas, and a wide soft halo.
	vec2 m = vec2(moon.x * aspect, moon.y);
	float d = length(p - m);
	float disc = smoothstep(0.047, 0.044, d);
	float seas = smoothstep(0.45, 0.65, fbm((p - m) * 38.0 + 3.0));
	float halo = exp(-d * 7.0) * 0.45 + exp(-d * 24.0) * 0.6;
	sky += moon_color * (disc * mix(2.4, 1.7, seas) + halo * 0.55);
	// Cloud: wind-driven, heavy in the rain, parting as the sky clears.
	vec2 q = vec2(p.x * 2.0 + TIME * 0.014, p.y * 4.2);
	float warp = fbm(q * 0.6 + vec2(TIME * 0.008, 0.0));
	float c = fbm(q + vec2(warp * 0.9, warp * 0.3));
	float cover = smoothstep(0.36 + clear * 0.2, 0.62 + clear * 0.26, c);
	cover *= 1.0 - 0.35 * smoothstep(0.75, 1.0, h);
	// The moon lights the cloud near it, most where it is thin.
	float near = exp(-d * 2.6);
	float thin = 1.0 - smoothstep(0.45, 0.9, c);
	vec3 cloud = mix(cloud_shadow, cloud_lit, near * (0.25 + 0.75 * thin));
	cloud += moon_color * exp(-d * 12.0) * thin * 0.5;
	COLOR = vec4(mix(sky, cloud, cover), 1.0);
}
