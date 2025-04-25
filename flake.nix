{
	inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-24.11";

	outputs = { nixpkgs, ... }:
	let
		pkgs = import nixpkgs { system = "x86_64-linux"; };
	in {
		devShells.x86_64-linux.default = pkgs.mkShell {
			buildInputs = [
				pkgs.python313
			];

			shellHook = ''
				export LD_LIBRARY_PATH=${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH
			'';
		};
	};
}