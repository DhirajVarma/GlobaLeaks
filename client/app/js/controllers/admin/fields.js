GLClient.controller("AdminFieldEditorCtrl", ["$scope",
  function($scope) {
    $scope.admin_receivers_by_id = $scope.Utils.array_to_map($scope.admin.users);

    $scope.editing = false;
    $scope.new_field = {};

    $scope.showAddTrigger = false;
    $scope.new_trigger = {};

    if ($scope.children) {
      $scope.fields = $scope.children;
    }

    $scope.children = $scope.field.children;

    $scope.parsedFields = $scope.fieldUtilities.parseFields($scope.fields);

    $scope.toggleEditing = function () {
      $scope.editing = !$scope.editing;
    };

    $scope.toggleAddTrigger = function () {
      $scope.showAddTrigger = !$scope.showAddTrigger;
    };

    $scope.isMarkableSubjectToStats = function(field) {
      return (["inputbox", "textarea", "fieldgroup"].indexOf(field.type) === -1);
    };

    $scope.isMarkableSubjectToPreview = function(field) {
      return (["fieldgroup", "fileupload"].indexOf(field.type) === -1);
    };

    $scope.typeSwitch = function (type) {
      if (["inputbox", "textarea"].indexOf(type) !== -1) {
        return "inputbox_or_textarea";
      }

      if (["checkbox", "selectbox"].indexOf(type) !== -1) {
        return "checkbox_or_selectbox";
      }

      return type;
    };

    $scope.showConfiguration = function(field) {
      if (["checkbox", "date", "daterange", "fieldgroup", "inputbox", "map", "selectbox", "textarea", "tos"].indexOf(field.type) > -1) {
        return true;
      }

      if (field.instance === "template" && (["whistleblower_identity"].indexOf(field.id) > -1)) {
        return true;
      }

      return false;
    };

    $scope.showOptions = function(field) {
      if (["checkbox", "selectbox", "multichoice"].indexOf(field.type) > -1) {
        return true;
      }

      return false;
    };

    $scope.delField = function(field) {
      $scope.Utils.deleteDialog().then(function() {
        return $scope.Utils.deleteResource($scope.fieldResource, $scope.fields, field);
      });
    };

    $scope.showAddQuestion = $scope.showAddQuestionFromTemplate = false;
    $scope.toggleAddQuestion = function() {
      $scope.showAddQuestion = !$scope.showAddQuestion;
      $scope.showAddQuestionFromTemplate = false;
    };

    $scope.toggleAddQuestionFromTemplate = function() {
      $scope.showAddQuestionFromTemplate = !$scope.showAddQuestionFromTemplate;
      $scope.showAddQuestion = false;
    };

    $scope.addOption = function () {
      var new_option = {
        "id": "",
        "label": "",
        "hint1": "",
        "hint2": "",
        "block_submission": false,
        "score_points": 0,
        "score_type": 0,
	"trigger_receiver": []
      };

      new_option.presentation_order = $scope.newItemOrder($scope.field.options, "presentation_order");

      $scope.field.options.push(new_option);
    };

    function swapOption(index, n) {
      var target = index + n;
      if (target < 0 || target >= $scope.field.options.length) {
        return;
      }
      var a = $scope.field.options[target];
      var b = $scope.field.options[index];
      $scope.field.options[target] = b;
      $scope.field.options[index] = a;
    }

    $scope.moveOptionUp = function(idx) { swapOption(idx, -1); };
    $scope.moveOptionDown = function(idx) { swapOption(idx, 1); };

    $scope.delOption = function(option) {
      $scope.field.options.splice($scope.field.options.indexOf(option), 1);
    };

    $scope.delTrigger = function(trigger) {
      $scope.field.triggered_by_options.splice($scope.field.triggered_by_options.indexOf(trigger), 1);
    };

    $scope.save_field = function(field) {
      field = new $scope.fieldResource(field);

      $scope.Utils.assignUniqueOrderIndex(field.options);

      return $scope.Utils.update(field);
    };

    $scope.moveUpAndSave = function(elem) {
      $scope.Utils.moveUp(elem);
      $scope.save_field(elem);
    };

    $scope.moveDownAndSave = function(elem) {
      $scope.Utils.moveDown(elem);
      $scope.save_field(elem);
    };

    $scope.moveLeftAndSave = function(elem) {
      $scope.Utils.moveLeft(elem);
      $scope.save_field(elem);
    };

    $scope.moveRightAndSave = function(elem) {
      $scope.Utils.moveRight(elem);
      $scope.save_field(elem);
    };

    $scope.add_field = function() {
      var field = $scope.AdminUtils.new_field("", $scope.field.id);
      field.label = $scope.new_field.label;
      field.type = $scope.new_field.type;
      field.attrs = $scope.admin.get_field_attrs(field.type);
      field.y = $scope.newItemOrder($scope.field.children, "y");

      field.instance = $scope.field.instance;

      if (field.type === "fileupload") {
        field.multi_entry = true;
      }

      field.$save(function(new_field){
        $scope.field.children.push(new_field);
        $scope.new_field = {};
      });
    };

    $scope.add_field_from_template = function() {
      var field = $scope.AdminUtils.new_field("", $scope.field.id);
      field.template_id = $scope.new_field.template_id;
      field.instance = "reference";
      field.y = $scope.newItemOrder($scope.field.children, "y");

      field.$save(function(new_field){
        $scope.field.children.push(new_field);
	$scope.new_field = {};
      });
    };

    $scope.fieldIsMarkableSubjectToStats = $scope.isMarkableSubjectToStats($scope.field);
    $scope.fieldIsMarkableSubjectToPreview = $scope.isMarkableSubjectToPreview($scope.field);

    $scope.addTrigger = function() {
      $scope.field.triggered_by_options.push($scope.new_trigger);
      $scope.toggleAddTrigger();
      $scope.new_trigger = {};
    };

    $scope.flipBlockSubmission = function(option) {
      option.block_submission = !option.block_submission;
    };

    $scope.addOptionHintDialog = function(option) {
      return $scope.Utils.openConfirmableModalDialog("views/partials/add_option_hint.html", option, $scope);
    };

    $scope.triggerReceiverDialog = function(option) {
      $scope.addReceiver = function(rec) {
        option.trigger_receiver.push(rec.id);
      };

      $scope.receiverNotSelectedFilter = function(item) {
        return option.trigger_receiver.indexOf(item.id) === -1;
      };

      return $scope.Utils.openConfirmableModalDialog("views/partials/trigger_receiver.html", option, $scope);
    };

    $scope.assignScorePointsDialog = function(option) {
      return $scope.Utils.openConfirmableModalDialog("views/partials/assign_score_points.html", option, $scope);
    };
  }
]).
controller("AdminFieldTemplatesCtrl", ["$scope", "AdminFieldTemplateResource",
  function($scope, AdminFieldTemplateResource) {
    $scope.fieldResource = AdminFieldTemplateResource;

    $scope.admin.fieldtemplates.$promise.then(function(fields) {
      $scope.fields = fields;
    });
  }
]).
controller("AdminFieldTemplatesAddCtrl", ["$scope",
  function($scope) {
    $scope.new_field = {};

    $scope.add_field = function() {
      var field = $scope.AdminUtils.new_field_template($scope.field ? $scope.field.id : "");
      field.instance = "template";
      field.label = $scope.new_field.label;
      field.type = $scope.new_field.type;
      field.attrs = $scope.admin.get_field_attrs(field.type);

      field.$save(function(new_field){
        $scope.fields.push(new_field);
        $scope.new_field = {};
      });
    };
  }
]);
