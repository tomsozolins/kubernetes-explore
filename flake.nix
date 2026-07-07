{
  description = "Dev environment for the kubernetes-explore Claude Code plugin";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python313
            uv
          ];
          shellHook = ''
            # uv must use the flake's interpreter, not download its own
            export UV_PYTHON="$(command -v python3.13)"
            export UV_PYTHON_DOWNLOADS=never
            # Materialize .venv so pytest is on PATH in-shell and the editor's
            # language server has a stable interpreter to resolve imports against.
            uv sync --frozen --quiet
            source .venv/bin/activate
          '';
        };
      });
}
