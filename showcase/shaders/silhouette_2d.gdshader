// Distant land in the 2D showcase: dark at its crest, melting into the sea mist towards its
// foot, with a little grain so it does not read as flat paint. The polygon's colour is the
// crest; `mist` is the colour at `foot_y` (world y).
shader_type canvas_item;
render_mode unshaded;

#include "res://showcase/shaders/noise.gdshaderinc"

uniform vec3 mist : source_color = vec3(0.12, 0.15, 0.23);
uniform float crest_y = 400.0;
uniform float foot_y = 700.0;

varying vec2 world;

void vertex() {
	world = (MODEL_MATRIX * vec4(VERTEX, 0.0, 1.0)).xy;
}

void fragment() {
	float t = smoothstep(crest_y, foot_y, world.y);
	vec3 colour = mix(COLOR.rgb, mist, t * t);
	colour *= 0.9 + 0.12 * fbm(world * 0.02);
	COLOR = vec4(colour, COLOR.a);
}
