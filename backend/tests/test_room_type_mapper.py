"""Tests for room type mapper — English, French, CubiCasa, DXF, IFC patterns."""

import pytest

from app.schemas.floor_plan import RoomType
from app.services.ingestion.room_type_mapper import (
    area_to_size_bucket,
    estimate_area_from_polygon,
    map_cubicasa_class_to_room_type,
    map_dxf_layer_to_room_type,
    map_ifc_name_to_room_type,
    map_label_to_room_type,
)


# --- English labels ---

class TestMapLabelEnglish:
    def test_living_room(self):
        assert map_label_to_room_type("Living Room") == RoomType.LIVING

    def test_kitchen(self):
        assert map_label_to_room_type("Kitchen") == RoomType.KITCHEN

    def test_bedroom(self):
        assert map_label_to_room_type("Bedroom") == RoomType.BEDROOM

    def test_master_bedroom(self):
        assert map_label_to_room_type("Master Bedroom") == RoomType.MASTER_BEDROOM

    def test_bathroom(self):
        assert map_label_to_room_type("Bathroom") == RoomType.BATHROOM

    def test_master_bathroom(self):
        assert map_label_to_room_type("Master Bathroom") == RoomType.MASTER_BATHROOM

    def test_half_bath(self):
        assert map_label_to_room_type("Half Bath") == RoomType.HALF_BATH

    def test_powder_room(self):
        assert map_label_to_room_type("Powder Room") == RoomType.HALF_BATH

    def test_dining(self):
        assert map_label_to_room_type("Dining Room") == RoomType.DINING

    def test_office(self):
        assert map_label_to_room_type("Office") == RoomType.OFFICE

    def test_garage(self):
        assert map_label_to_room_type("Garage") == RoomType.GARAGE

    def test_foyer(self):
        assert map_label_to_room_type("Foyer") == RoomType.FOYER

    def test_hallway(self):
        assert map_label_to_room_type("Hallway") == RoomType.HALLWAY

    def test_laundry(self):
        assert map_label_to_room_type("Laundry Room") == RoomType.LAUNDRY

    def test_closet(self):
        assert map_label_to_room_type("Closet") == RoomType.CLOSET

    def test_walkin_closet(self):
        assert map_label_to_room_type("Walk-in Closet") == RoomType.WALKIN_CLOSET

    def test_storage(self):
        assert map_label_to_room_type("Storage") == RoomType.STORAGE

    def test_pantry(self):
        assert map_label_to_room_type("Pantry") == RoomType.PANTRY

    def test_staircase(self):
        assert map_label_to_room_type("Staircase") == RoomType.STAIRCASE

    def test_balcony(self):
        assert map_label_to_room_type("Balcony") == RoomType.BALCONY

    def test_porch(self):
        assert map_label_to_room_type("Porch") == RoomType.PORCH

    def test_unknown(self):
        assert map_label_to_room_type("XYZ Unknown Room") is None

    def test_empty(self):
        assert map_label_to_room_type("") is None


# --- French labels ---

class TestMapLabelFrench:
    def test_salon(self):
        assert map_label_to_room_type("Salon") == RoomType.LIVING

    def test_cuisine(self):
        assert map_label_to_room_type("Cuisine") == RoomType.KITCHEN

    def test_chambre(self):
        assert map_label_to_room_type("Chambre") == RoomType.BEDROOM

    def test_chambre_principale(self):
        assert map_label_to_room_type("Chambre Principale") == RoomType.MASTER_BEDROOM

    def test_salle_de_bain(self):
        assert map_label_to_room_type("Salle de bain") == RoomType.BATHROOM

    def test_sdb(self):
        assert map_label_to_room_type("SDB") == RoomType.BATHROOM

    def test_bureau(self):
        assert map_label_to_room_type("Bureau") == RoomType.OFFICE

    def test_buanderie(self):
        assert map_label_to_room_type("Buanderie") == RoomType.LAUNDRY

    def test_entree(self):
        assert map_label_to_room_type("Entrée") == RoomType.FOYER

    def test_couloir(self):
        assert map_label_to_room_type("Couloir") == RoomType.HALLWAY

    def test_salle_a_manger(self):
        assert map_label_to_room_type("Salle à manger") == RoomType.DINING


# --- CubiCasa class indices ---

class TestCubiCasaMapping:
    def test_background(self):
        assert map_cubicasa_class_to_room_type(0) is None

    def test_outdoor(self):
        assert map_cubicasa_class_to_room_type(1) is None

    def test_wall(self):
        assert map_cubicasa_class_to_room_type(2) is None

    def test_kitchen(self):
        assert map_cubicasa_class_to_room_type(3) == RoomType.KITCHEN

    def test_living(self):
        assert map_cubicasa_class_to_room_type(4) == RoomType.LIVING

    def test_bedroom(self):
        assert map_cubicasa_class_to_room_type(5) == RoomType.BEDROOM

    def test_bathroom(self):
        assert map_cubicasa_class_to_room_type(6) == RoomType.BATHROOM

    def test_entry(self):
        assert map_cubicasa_class_to_room_type(7) == RoomType.FOYER

    def test_railing(self):
        assert map_cubicasa_class_to_room_type(8) is None

    def test_storage(self):
        assert map_cubicasa_class_to_room_type(9) == RoomType.STORAGE

    def test_garage(self):
        assert map_cubicasa_class_to_room_type(10) == RoomType.GARAGE

    def test_undefined(self):
        assert map_cubicasa_class_to_room_type(11) == RoomType.OTHER

    def test_out_of_range(self):
        assert map_cubicasa_class_to_room_type(99) is None


# --- DXF layer names ---

class TestDXFLayerMapping:
    def test_a_room_kitchen(self):
        assert map_dxf_layer_to_room_type("A-ROOM-KITCHEN") == RoomType.KITCHEN

    def test_bedroom_1(self):
        assert map_dxf_layer_to_room_type("BEDROOM_1") == RoomType.BEDROOM

    def test_room_living(self):
        assert map_dxf_layer_to_room_type("ROOM_LIVING") == RoomType.LIVING

    def test_a_space_bathroom(self):
        assert map_dxf_layer_to_room_type("A-SPACE-BATHROOM") == RoomType.BATHROOM

    def test_garage_2(self):
        assert map_dxf_layer_to_room_type("ROOM_GARAGE_2") == RoomType.GARAGE

    def test_unrelated_layer(self):
        assert map_dxf_layer_to_room_type("DIMENSIONS") is None


# --- IFC names ---

class TestIFCNameMapping:
    def test_living_room(self):
        assert map_ifc_name_to_room_type("Living Room") == RoomType.LIVING

    def test_kitchen_ifc(self):
        assert map_ifc_name_to_room_type("Kitchen") == RoomType.KITCHEN

    def test_master_bed(self):
        assert map_ifc_name_to_room_type("Master Bedroom Suite") == RoomType.MASTER_BEDROOM


# --- Area utilities ---

class TestAreaUtilities:
    def test_estimate_area_square(self):
        # 3x4 rectangle = 12 sqm
        coords = [(0, 0), (3, 0), (3, 4), (0, 4), (0, 0)]
        assert abs(estimate_area_from_polygon(coords) - 12.0) < 0.01

    def test_estimate_area_triangle(self):
        coords = [(0, 0), (4, 0), (2, 3), (0, 0)]
        assert abs(estimate_area_from_polygon(coords) - 6.0) < 0.01

    def test_estimate_area_too_few_points(self):
        assert estimate_area_from_polygon([(0, 0), (1, 0)]) == 0.0

    def test_size_bucket_xs(self):
        assert area_to_size_bucket(2.0) == "XS"

    def test_size_bucket_s(self):
        assert area_to_size_bucket(6.0) == "S"

    def test_size_bucket_m(self):
        assert area_to_size_bucket(12.0) == "M"

    def test_size_bucket_l(self):
        assert area_to_size_bucket(20.0) == "L"

    def test_size_bucket_xl(self):
        assert area_to_size_bucket(30.0) == "XL"
